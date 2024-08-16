import logging
import queue
import threading
import time
from collections.abc import Generator, Mapping
from typing import Any, Optional

from flask import Flask, current_app
from uritemplate.variable import VariableValue

from core.app.apps.base_app_queue_manager import GenerateTaskStoppedException
from core.app.entities.app_invoke_entities import InvokeFrom
from core.workflow.entities.node_entities import (
    NodeRunMetadataKey,
    NodeType,
    UserFrom,
)
from core.workflow.entities.variable_pool import VariablePool
from core.workflow.graph_engine.condition_handlers.condition_manager import ConditionManager
from core.workflow.graph_engine.entities.event import (
    BaseIterationEvent,
    GraphEngineEvent,
    GraphRunFailedEvent,
    GraphRunStartedEvent,
    GraphRunSucceededEvent,
    NodeRunFailedEvent,
    NodeRunRetrieverResourceEvent,
    NodeRunStartedEvent,
    NodeRunStreamChunkEvent,
    NodeRunSucceededEvent,
    ParallelBranchRunFailedEvent,
    ParallelBranchRunStartedEvent,
    ParallelBranchRunSucceededEvent,
)
from core.workflow.graph_engine.entities.graph import Graph
from core.workflow.graph_engine.entities.graph_init_params import GraphInitParams
from core.workflow.graph_engine.entities.graph_runtime_state import GraphRuntimeState
from core.workflow.graph_engine.entities.runtime_route_state import RouteNodeState
from core.workflow.nodes.answer.answer_stream_processor import AnswerStreamProcessor
from core.workflow.nodes.base_node import BaseNode
from core.workflow.nodes.end.end_stream_processor import EndStreamProcessor
from core.workflow.nodes.event import RunCompletedEvent, RunRetrieverResourceEvent, RunStreamChunkEvent
from core.workflow.nodes.node_mapping import node_classes
from extensions.ext_database import db
from models.workflow import WorkflowNodeExecutionStatus, WorkflowType

logger = logging.getLogger(__name__)


class GraphEngine:
    def __init__(
            self,
            tenant_id: str,
            app_id: str,
            workflow_type: WorkflowType,
            workflow_id: str,
            user_id: str,
            user_from: UserFrom,
            invoke_from: InvokeFrom,
            call_depth: int,
            graph: Graph,
            graph_config: Mapping[str, Any],
            variable_pool: VariablePool,
            max_execution_steps: int,
            max_execution_time: int
    ) -> None:
        self.graph = graph
        self.init_params = GraphInitParams(
            tenant_id=tenant_id,
            app_id=app_id,
            workflow_type=workflow_type,
            workflow_id=workflow_id,
            graph_config=graph_config,
            user_id=user_id,
            user_from=user_from,
            invoke_from=invoke_from,
            call_depth=call_depth
        )

        self.graph_runtime_state = GraphRuntimeState(
            variable_pool=variable_pool,
            start_at=time.perf_counter()
        )

        self.max_execution_steps = max_execution_steps
        self.max_execution_time = max_execution_time

    def run(self) -> Generator[GraphEngineEvent, None, None]:
        # trigger graph run start event
        yield GraphRunStartedEvent()

        try:
            stream_processor_cls: type[AnswerStreamProcessor | EndStreamProcessor]
            if self.init_params.workflow_type == WorkflowType.CHAT:
                stream_processor_cls = AnswerStreamProcessor
            else:
                stream_processor_cls = EndStreamProcessor

            stream_processor = stream_processor_cls(
                graph=self.graph,
                variable_pool=self.graph_runtime_state.variable_pool
            )

            # run graph
            generator = stream_processor.process(
                self._run(start_node_id=self.graph.root_node_id)
            )

            for item in generator:
                try:
                    yield item
                    if isinstance(item, NodeRunFailedEvent):
                        yield GraphRunFailedEvent(error=item.route_node_state.failed_reason or 'Unknown error.')
                        return
                    elif isinstance(item, NodeRunSucceededEvent):
                        if item.node_type == NodeType.END:
                            self.graph_runtime_state.outputs = (item.route_node_state.node_run_result.outputs
                                             if item.route_node_state.node_run_result
                                                and item.route_node_state.node_run_result.outputs
                                             else {})
                        elif item.node_type == NodeType.ANSWER:
                            if "answer" not in self.graph_runtime_state.outputs:
                                self.graph_runtime_state.outputs["answer"] = ""

                            self.graph_runtime_state.outputs["answer"] += "\n" + (item.route_node_state.node_run_result.outputs.get("answer", "")
                                                               if item.route_node_state.node_run_result
                                                                  and item.route_node_state.node_run_result.outputs
                                                               else "")
                        
                            self.graph_runtime_state.outputs["answer"] = self.graph_runtime_state.outputs["answer"].strip()
                except Exception as e:
                    logger.exception(f"Graph run failed: {str(e)}")
                    yield GraphRunFailedEvent(error=str(e))
                    return

            # trigger graph run success event
            yield GraphRunSucceededEvent(outputs=self.graph_runtime_state.outputs)
        except GraphRunFailedError as e:
            yield GraphRunFailedEvent(error=e.error)
            return
        except Exception as e:
            logger.exception("Unknown Error when graph running")
            yield GraphRunFailedEvent(error=str(e))
            raise e

    def _run(self, start_node_id: str, in_parallel_id: Optional[str] = None) -> Generator[GraphEngineEvent, None, None]:
        parallel_start_node_id = None
        if in_parallel_id:
            parallel_start_node_id = start_node_id

        next_node_id = start_node_id
        previous_route_node_state: Optional[RouteNodeState] = None
        while True:
            # max steps reached
            if self.graph_runtime_state.node_run_steps > self.max_execution_steps:
                raise GraphRunFailedError('Max steps {} reached.'.format(self.max_execution_steps))

            # or max execution time reached
            if self._is_timed_out(
                    start_at=self.graph_runtime_state.start_at,
                    max_execution_time=self.max_execution_time
            ):
                raise GraphRunFailedError('Max execution time {}s reached.'.format(self.max_execution_time))

            # init route node state
            route_node_state = self.graph_runtime_state.node_run_state.create_node_state(
                node_id=next_node_id
            )

            # get node config
            node_id = route_node_state.node_id
            node_config = self.graph.node_id_config_mapping.get(node_id)
            if not node_config:
                raise GraphRunFailedError(f'Node {node_id} config not found.')

            # convert to specific node
            node_type = NodeType.value_of(node_config.get('data', {}).get('type'))
            node_cls = node_classes.get(node_type)
            if not node_cls:
                raise GraphRunFailedError(f'Node {node_id} type {node_type} not found.')

            previous_node_id = previous_route_node_state.node_id if previous_route_node_state else None

            # init workflow run state
            node_instance = node_cls(  # type: ignore
                id=route_node_state.id,
                config=node_config,
                graph_init_params=self.init_params,
                graph=self.graph,
                graph_runtime_state=self.graph_runtime_state,
                previous_node_id=previous_node_id
            )

            try:
                # run node
                generator = self._run_node(
                    node_instance=node_instance,
                    route_node_state=route_node_state,
                    parallel_id=in_parallel_id,
                    parallel_start_node_id=parallel_start_node_id
                )

                for item in generator:
                    if isinstance(item, NodeRunStartedEvent):
                        self.graph_runtime_state.node_run_steps += 1
                        item.route_node_state.index = self.graph_runtime_state.node_run_steps

                    yield item

                self.graph_runtime_state.node_run_state.node_state_mapping[route_node_state.id] = route_node_state

                # append route
                if previous_route_node_state:
                    self.graph_runtime_state.node_run_state.add_route(
                        source_node_state_id=previous_route_node_state.id,
                        target_node_state_id=route_node_state.id
                    )
            except Exception as e:
                route_node_state.status = RouteNodeState.Status.FAILED
                route_node_state.failed_reason = str(e)
                yield NodeRunFailedEvent(
                    error=str(e),
                    id=node_instance.id,
                    node_id=next_node_id,
                    node_type=node_type,
                    node_data=node_instance.node_data,
                    route_node_state=route_node_state,
                    parallel_id=in_parallel_id,
                    parallel_start_node_id=parallel_start_node_id
                )
                raise e

            # It may not be necessary, but it is necessary. :)
            if (self.graph.node_id_config_mapping[next_node_id]
                    .get("data", {}).get("type", "").lower() == NodeType.END.value):
                break

            previous_route_node_state = route_node_state

            # get next node ids
            edge_mappings = self.graph.edge_mapping.get(next_node_id)
            if not edge_mappings:
                break

            if len(edge_mappings) == 1:
                edge = edge_mappings[0]

                if edge.run_condition:
                    result = ConditionManager.get_condition_handler(
                        init_params=self.init_params,
                        graph=self.graph,
                        run_condition=edge.run_condition,
                    ).check(
                        graph_runtime_state=self.graph_runtime_state,
                        previous_route_node_state=previous_route_node_state,
                        target_node_id=edge.target_node_id,
                    )

                    if not result:
                        break

                next_node_id = edge.target_node_id
            else:
                if any(edge.run_condition for edge in edge_mappings):
                    # if nodes has run conditions, get node id which branch to take based on the run condition results
                    final_node_id = None
                    for edge in edge_mappings:
                        if edge.run_condition:
                            result = ConditionManager.get_condition_handler(
                                init_params=self.init_params,
                                graph=self.graph,
                                run_condition=edge.run_condition,
                            ).check(
                                graph_runtime_state=self.graph_runtime_state,
                                previous_route_node_state=previous_route_node_state,
                                target_node_id=edge.target_node_id,
                            )

                            if result:
                                final_node_id = edge.target_node_id
                                break

                    if not final_node_id:
                        break

                    next_node_id = final_node_id
                else:
                    # if nodes has no run conditions, parallel run all nodes
                    parallel_id = self.graph.node_parallel_mapping.get(edge_mappings[0].target_node_id)
                    if not parallel_id:
                        raise GraphRunFailedError(f'Node {edge_mappings[0].target_node_id} related parallel not found.')

                    parallel = self.graph.parallel_mapping.get(parallel_id)
                    if not parallel:
                        raise GraphRunFailedError(f'Parallel {parallel_id} not found.')

                    # run parallel nodes, run in new thread and use queue to get results
                    q: queue.Queue = queue.Queue()

                    # Create a list to store the threads
                    threads = []

                    # new thread
                    for edge in edge_mappings:
                        thread = threading.Thread(target=self._run_parallel_node, kwargs={
                            'flask_app': current_app._get_current_object(),  # type: ignore[attr-defined]
                            'parallel_id': parallel_id,
                            'parallel_start_node_id': edge.target_node_id,
                            'q': q
                        })

                        threads.append(thread)
                        thread.start()

                    succeeded_count = 0
                    while True:
                        try:
                            event = q.get(timeout=1)
                            if event is None:
                                break

                            yield event
                            if isinstance(event, NodeRunSucceededEvent) and event.node_data.title == 'LLM 4':
                                print("LLM 4 succeeded")

                            if event.parallel_id == parallel_id:
                                if isinstance(event, ParallelBranchRunSucceededEvent):
                                    succeeded_count += 1
                                    if succeeded_count == len(threads):
                                        q.put(None)

                                    continue
                                elif isinstance(event, ParallelBranchRunFailedEvent):
                                    raise GraphRunFailedError(event.error)
                        except queue.Empty:
                            continue

                    # Join all threads
                    for thread in threads:
                        thread.join()

                    # get final node id
                    final_node_id = parallel.end_to_node_id
                    if not final_node_id:
                        break

                    next_node_id = final_node_id

            if in_parallel_id and self.graph.node_parallel_mapping.get(next_node_id, '') != in_parallel_id:
                break

    def _run_parallel_node(self,
                           flask_app: Flask,
                           parallel_id: str,
                           parallel_start_node_id: str,
                           q: queue.Queue) -> None:
        """
        Run parallel nodes
        """
        with flask_app.app_context():
            try:
                q.put(ParallelBranchRunStartedEvent(
                    parallel_id=parallel_id,
                    parallel_start_node_id=parallel_start_node_id
                ))

                # run node
                generator = self._run(
                    start_node_id=parallel_start_node_id,
                    in_parallel_id=parallel_id
                )

                for item in generator:
                    q.put(item)

                # trigger graph run success event
                q.put(ParallelBranchRunSucceededEvent(
                    parallel_id=parallel_id,
                    parallel_start_node_id=parallel_start_node_id
                ))
            except GraphRunFailedError as e:
                q.put(ParallelBranchRunFailedEvent(
                    parallel_id=parallel_id,
                    parallel_start_node_id=parallel_start_node_id,
                    error=e.error
                ))
            except Exception as e:
                logger.exception("Unknown Error when generating in parallel")
                q.put(ParallelBranchRunFailedEvent(
                    parallel_id=parallel_id,
                    parallel_start_node_id=parallel_start_node_id,
                    error=str(e)
                ))
            finally:
                db.session.remove()

    def _run_node(self,
                  node_instance: BaseNode,
                  route_node_state: RouteNodeState,
                  parallel_id: Optional[str] = None,
                  parallel_start_node_id: Optional[str] = None) -> Generator[GraphEngineEvent, None, None]:
        """
        Run node
        """
        # trigger node run start event
        yield NodeRunStartedEvent(
            id=node_instance.id,
            node_id=node_instance.node_id,
            node_type=node_instance.node_type,
            node_data=node_instance.node_data,
            route_node_state=route_node_state,
            predecessor_node_id=node_instance.previous_node_id,
            parallel_id=parallel_id,
            parallel_start_node_id=parallel_start_node_id
        )

        db.session.close()

        try:
            # run node
            generator = node_instance.run()
            for item in generator:
                if isinstance(item, GraphEngineEvent):
                    if isinstance(item, BaseIterationEvent):
                        # add parallel info to iteration event
                        item.parallel_id = parallel_id
                        item.parallel_start_node_id = parallel_start_node_id

                    yield item
                else:
                    if isinstance(item, RunCompletedEvent):
                        run_result = item.run_result
                        route_node_state.set_finished(run_result=run_result)

                        if run_result.status == WorkflowNodeExecutionStatus.FAILED:
                            yield NodeRunFailedEvent(
                                error=route_node_state.failed_reason or 'Unknown error.',
                                id=node_instance.id,
                                node_id=node_instance.node_id,
                                node_type=node_instance.node_type,
                                node_data=node_instance.node_data,
                                route_node_state=route_node_state,
                                parallel_id=parallel_id,
                                parallel_start_node_id=parallel_start_node_id
                            )
                        elif run_result.status == WorkflowNodeExecutionStatus.SUCCEEDED:
                            if run_result.metadata and run_result.metadata.get(NodeRunMetadataKey.TOTAL_TOKENS):
                                # plus state total_tokens
                                self.graph_runtime_state.total_tokens += int(
                                    run_result.metadata.get(NodeRunMetadataKey.TOTAL_TOKENS)  # type: ignore[arg-type]
                                )

                            if run_result.llm_usage:
                                # use the latest usage
                                self.graph_runtime_state.llm_usage += run_result.llm_usage

                            # append node output variables to variable pool
                            if run_result.outputs:
                                for variable_key, variable_value in run_result.outputs.items():
                                    # append variables to variable pool recursively
                                    self._append_variables_recursively(
                                        node_id=node_instance.node_id,
                                        variable_key_list=[variable_key],
                                        variable_value=variable_value
                                    )

                            # add parallel info to run result metadata
                            if parallel_id and parallel_start_node_id:
                                if not run_result.metadata:
                                    run_result.metadata = {}

                                run_result.metadata[NodeRunMetadataKey.PARALLEL_ID] = parallel_id
                                run_result.metadata[NodeRunMetadataKey.PARALLEL_START_NODE_ID] = parallel_start_node_id

                            yield NodeRunSucceededEvent(
                                id=node_instance.id,
                                node_id=node_instance.node_id,
                                node_type=node_instance.node_type,
                                node_data=node_instance.node_data,
                                route_node_state=route_node_state,
                                parallel_id=parallel_id,
                                parallel_start_node_id=parallel_start_node_id
                            )

                        break
                    elif isinstance(item, RunStreamChunkEvent):
                        yield NodeRunStreamChunkEvent(
                            id=node_instance.id,
                            node_id=node_instance.node_id,
                            node_type=node_instance.node_type,
                            node_data=node_instance.node_data,
                            chunk_content=item.chunk_content,
                            from_variable_selector=item.from_variable_selector,
                            route_node_state=route_node_state,
                            parallel_id=parallel_id,
                            parallel_start_node_id=parallel_start_node_id,
                        )
                    elif isinstance(item, RunRetrieverResourceEvent):
                        yield NodeRunRetrieverResourceEvent(
                            id=node_instance.id,
                            node_id=node_instance.node_id,
                            node_type=node_instance.node_type,
                            node_data=node_instance.node_data,
                            retriever_resources=item.retriever_resources,
                            context=item.context,
                            route_node_state=route_node_state,
                            parallel_id=parallel_id,
                            parallel_start_node_id=parallel_start_node_id,
                        )
        except GenerateTaskStoppedException:
            # trigger node run failed event
            route_node_state.status = RouteNodeState.Status.FAILED
            route_node_state.failed_reason = "Workflow stopped."
            yield NodeRunFailedEvent(
                error="Workflow stopped.",
                id=node_instance.id,
                node_id=node_instance.node_id,
                node_type=node_instance.node_type,
                node_data=node_instance.node_data,
                route_node_state=route_node_state,
                parallel_id=parallel_id,
                parallel_start_node_id=parallel_start_node_id,
            )
            return
        except Exception as e:
            logger.exception(f"Node {node_instance.node_data.title} run failed: {str(e)}")
            raise e
        finally:
            db.session.close()

    def _append_variables_recursively(self,
                                      node_id: str,
                                      variable_key_list: list[str],
                                      variable_value: VariableValue):
        """
        Append variables recursively
        :param node_id: node id
        :param variable_key_list: variable key list
        :param variable_value: variable value
        :return:
        """
        self.graph_runtime_state.variable_pool.add(
            [node_id] + variable_key_list,
            variable_value
        )

        # if variable_value is a dict, then recursively append variables
        if isinstance(variable_value, dict):
            for key, value in variable_value.items():
                # construct new key list
                new_key_list = variable_key_list + [key]
                self._append_variables_recursively(
                    node_id=node_id,
                    variable_key_list=new_key_list,
                    variable_value=value
                )

    def _is_timed_out(self, start_at: float, max_execution_time: int) -> bool:
        """
        Check timeout
        :param start_at: start time
        :param max_execution_time: max execution time
        :return:
        """
        return time.perf_counter() - start_at > max_execution_time


class GraphRunFailedError(Exception):
    def __init__(self, error: str):
        self.error = error