import axios from "axios";
import { ReadStream, createReadStream } from "node:fs";
import { mergeDefaultOptions } from "./utils.js";
import FormData from "form-data";

export const BASE_URL = "https://api.dify.ai/v1";

export const routes = {
  //  app's
  feedback: {
    method: "POST",
    url: (message_id) => `/messages/${message_id}/feedbacks`,
  },
  application: {
    method: "GET",
    url: () => `/parameters`,
  },
  fileUpload: {
    method: "POST",
    url: () => `/files/upload`,
  },
  textToAudio: {
    method: "POST",
    url: () => `/text-to-audio`,
  },
  getMeta: {
    method: "GET",
    url: () => `/meta`,
  },

  // completion's
  createCompletionMessage: {
    method: "POST",
    url: () => `/completion-messages`,
  },

  // chat's
  createChatMessage: {
    method: "POST",
    url: () => `/chat-messages`,
  },
  getSuggested: {
    method: "GET",
    url: (message_id) => `/messages/${message_id}/suggested`,
  },
  stopChatMessage: {
    method: "POST",
    url: (task_id) => `/chat-messages/${task_id}/stop`,
  },
  getConversations: {
    method: "GET",
    url: () => `/conversations`,
  },
  getConversationMessages: {
    method: "GET",
    url: () => `/messages`,
  },
  renameConversation: {
    method: "POST",
    url: (conversation_id) => `/conversations/${conversation_id}/name`,
  },
  deleteConversation: {
    method: "DELETE",
    url: (conversation_id) => `/conversations/${conversation_id}`,
  },
  audioToText: {
    method: "POST",
    url: () => `/audio-to-text`,
  },

  // workflow‘s
  runWorkflow: {
    method: "POST",
    url: () => `/workflows/run`,
  },
  stopWorkflow: {
    method: "POST",
    url: (task_id) => `/workflows/${task_id}/stop`,
  },

  // dataset'
  createDataset: {
    method: "POST",
    url: () => `/datasets`,
  },
  listDatasets: {
    method: "GET",
    url: () => `/datasets`,
  },
  deleteDataset: {
    method: "DELETE",
    url: (dataset_id) => `/datasets/${dataset_id}`,
  },
  createDocumentByText: {
    method: "POST",
    url: (dataset_id) => `/datasets/${dataset_id}/document/create_by_text`,
  },
  createDocumentByFile: {
    method: "POST",
    url: (dataset_id) => `/datasets/${dataset_id}/document/create_by_file`,
  },
  updateDocumentByText: {
    method: "POST",
    url: (dataset_id, document_id) =>
      `/datasets/${dataset_id}/documents/${document_id}/update_by_text`,
  },
  updateDocumentByFile: {
    method: "POST",
    url: (dataset_id, document_id) =>
      `/datasets/${dataset_id}/documents/${document_id}/update_by_file`,
  },
  getDocumentEmbeddingStatus: {
    method: "GET",
    url: (dataset_id, batch) =>
      `/datasets/${dataset_id}/documents/${batch}/indexing-status`,
  },
  deleteDocument: {
    method: "DELETE",
    url: (dataset_id, document_id) =>
      `/datasets/${dataset_id}/documents/${document_id}`,
  },
  listDocuments: {
    method: "GET",
    url: (dataset_id) => `/datasets/${dataset_id}/documents`,
  },
  addDocumentSegment: {
    method: "POST",
    url: (dataset_id, document_id) => `/datasets/${dataset_id}/documents/${document_id}/segments`,
  },
  getDocumentSegments: {
    method: "GET",
    url: (dataset_id, document_id) => `/datasets/${dataset_id}/documents/${document_id}/segments`,
  },
  deleteDocumentSegment: {
    method: "DELETE",
    url: (dataset_id, document_id, segment_id) => `/datasets/${dataset_id}/documents/${document_id}/segments/${segment_id}`,
  },
  updateDocumentSegment: {
    method: "POST",
    url: (dataset_id, document_id, segment_id) => `/datasets/${dataset_id}/documents/${document_id}/segments/${segment_id}`,
  },
};

export class DifyClient {
  constructor(apiKey, baseUrl = BASE_URL) {
    this.apiKey = apiKey;
    this.baseUrl = baseUrl;
  }

  updateApiKey(apiKey) {
    this.apiKey = apiKey;
  }

  async sendRequest(
    method,
    endpoint,
    data = null,
    params = null,
    stream = false,
    headerParams = {}
  ) {
    const headers = {
      ...{
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      ...headerParams
    };

    const url = `${this.baseUrl}${endpoint}`;
    let response;
    if (stream) {
      response = await axios({
        method,
        url,
        data,
        params,
        headers,
        responseType: "stream",
        validateStatus: false,
      });
    } else {
      response = await axios({
        method,
        url,
        ...(method !== "GET" && { data }),
        params,
        headers,
        responseType: "json",
        validateStatus: false,
      });
    }

    if (!response) {
      throw new Error("No response from server");
    }

    if (response?.data.code !== undefined && response.data.code !== 200) {
      throw new Error(
        `${response.data.status} ${response.data.code}: ${response.data.message}`
      );
    }

    return response.data;
  }

  messageFeedback(message_id, rating, user) {
    const data = {
      rating,
      user,
    };
    return this.sendRequest(
      routes.feedback.method,
      routes.feedback.url(message_id),
      data,
    );
  }

  getApplicationParameters(user) {
    const params = { user };
    return this.sendRequest(
      routes.application.method,
      routes.application.url(),
      null,
      params
    );
  }

  fileUpload(data) {
    return this.sendRequest(
      routes.fileUpload.method,
      routes.fileUpload.url(),
      data,
      null,
      false,
      {
        "Content-Type": 'multipart/form-data'
      }
    );
  }

  textToAudio(text, user, streaming = false) {
    const data = {
      text,
      user,
      streaming
    };
    return this.sendRequest(
      routes.textToAudio.method,
      routes.textToAudio.url(),
      data,
      null,
      streaming
    );
  }

  getMeta(user) {
    const params = { user };
    return this.sendRequest(
      routes.meta.method,
      routes.meta.url(),
      null,
      params
    );
  }
}

export class CompletionClient extends DifyClient {
  createCompletionMessage(inputs, user, stream = false, files = null) {
    const data = {
      inputs,
      user,
      response_mode: stream ? "streaming" : "blocking",
      files,
    };
    return this.sendRequest(
      routes.createCompletionMessage.method,
      routes.createCompletionMessage.url(),
      data,
      null,
      stream
    );
  }

  runWorkflow(inputs, user, stream = false, files = null) {
    const data = {
      inputs,
      user,
      response_mode: stream ? "streaming" : "blocking",
    };
    return this.sendRequest(
      routes.runWorkflow.method,
      routes.runWorkflow.url(),
      data,
      null,
      stream
    );
  }
}

export class ChatClient extends DifyClient {
  createChatMessage(
    inputs,
    query,
    user,
    stream = false,
    conversation_id = null,
    files = null
  ) {
    const data = {
      inputs,
      query,
      user,
      response_mode: stream ? "streaming" : "blocking",
      files,
    };
    if (conversation_id) data.conversation_id = conversation_id;

    return this.sendRequest(
      routes.createChatMessage.method,
      routes.createChatMessage.url(),
      data,
      null,
      stream
    );
  }

  getSuggested(message_id, user) {
    const data = { user };
    return this.sendRequest(
      routes.getSuggested.method,
      routes.getSuggested.url(message_id),
      data
    );
  }

  stopMessage(task_id, user) {
    const data = { user };
    return this.sendRequest(
      routes.stopChatMessage.method,
      routes.stopChatMessage.url(task_id),
      data
    );
  }

  getConversations(user, first_id = null, limit = null, pinned = null) {
    const params = { user, first_id: first_id, limit, pinned };
    return this.sendRequest(
      routes.getConversations.method,
      routes.getConversations.url(),
      null,
      params
    );
  }

  getConversationMessages(
    user,
    conversation_id = "",
    first_id = null,
    limit = null
  ) {
    const params = { user };

    if (conversation_id) params.conversation_id = conversation_id;

    if (first_id) params.first_id = first_id;

    if (limit) params.limit = limit;

    return this.sendRequest(
      routes.getConversationMessages.method,
      routes.getConversationMessages.url(),
      null,
      params
    );
  }

  renameConversation(conversation_id, name, user, auto_generate) {
    const data = { name, user, auto_generate };
    return this.sendRequest(
      routes.renameConversation.method,
      routes.renameConversation.url(conversation_id),
      data
    );
  }

  deleteConversation(conversation_id, user) {
    const data = { user };
    return this.sendRequest(
      routes.deleteConversation.method,
      routes.deleteConversation.url(conversation_id),
      data
    );
  }


  audioToText(data) {
    return this.sendRequest(
      routes.audioToText.method,
      routes.audioToText.url(),
      data,
      null,
      false,
      {
        "Content-Type": 'multipart/form-data'
      }
    );
  }
}

export class WorkflowClient extends DifyClient {
  run(inputs, user, stream) {
    const data = {
      inputs,
      response_mode: stream ? "streaming" : "blocking",
      user
    };

    return this.sendRequest(
      routes.runWorkflow.method,
      routes.runWorkflow.url(),
      data,
      null,
      stream
    );
  }

  stop(task_id, user) {
    const data = { user };
    return this.sendRequest(
      routes.stopWorkflow.method,
      routes.stopWorkflow.url(task_id),
      data
    );
  }
}

export class DatasetClient extends DifyClient {
  async createDataset(name, options) {
    const { permission } = options ?? {};
    const data = { name, permission };

    return this.sendRequest(
      routes.createDataset.method,
      routes.createDataset.url(),
      data,
    );
  }

  async listDatasets(params) {
    return this.sendRequest(
      routes.listDatasets.method,
      routes.listDatasets.url(),
      null,
      params,
    );
  }

  async deleteDataset(dataset_id) {
    return this.sendRequest(
      routes.deleteDataset.method,
      routes.deleteDataset.url(dataset_id),
    );
  }

  async createDocumentByText(dataset_id, options) {
    const defaultOptions = {
      process_rule: {
        rules: {
          pre_processing_rules: [],
          segmentation: {
            separator: "\n",
            max_tokens: 1000,
          }
        }
      }
    }

    return this.sendRequest(
      routes.createDocumentByText.method,
      routes.createDocumentByText.url(dataset_id),
      mergeDefaultOptions(defaultOptions, options),
    );
  }

  async createDocumentByFile(dataset_id, options) {
    if (!options?.file) {
      throw new Error("No file provided");
    }
    let readStream;
    if (typeof options.file === "string") {
      readStream = createReadStream(options.file);
    } else if (options.file instanceof ReadStream) {
      readStream = options.file;
    } else {
      throw new Error("Invalid file provided. Accepts string or ReadStream");
    }
    const defaultOptions = {
      process_rule: {
        rules: {
          pre_processing_rules: [],
          segmentation: {
            separator: "\n",
            max_tokens: 1000,
          }
        }
      }
    }
    // Make a copy of options without the file property
    const _options = { ...options };
    delete _options.file;

    const data = new FormData();

    data.append("file", readStream);
    data.append("data", JSON.stringify(mergeDefaultOptions(defaultOptions, _options)));

    return this.sendRequest(
      routes.createDocumentByFile.method,
      routes.createDocumentByFile.url(dataset_id),
      data,
      null,
      false,
      data.getHeaders(),
    )
  }

  async updateDocumentByText(dataset_id, document_id, options) {
    return this.sendRequest(
      routes.updateDocumentByText.method,
      routes.updateDocumentByText.url(dataset_id, document_id),
      options,
    );
  }

  async updateDocumentByFile(dataset_id, document_id, options) {
    if (!options?.file) {
      throw new Error("No file provided");
    }
    let readStream;
    if (typeof options.file === "string") {
      readStream = createReadStream(options.file);
    } else if (options.file instanceof ReadStream) {
      readStream = options.file;
    } else {
      throw new Error("Invalid file provided. Accepts string or ReadStream");
    }

    const data = new FormData();
    data.append("file", readStream);
    if (options.name) {
      data.append("name", options.name);
    }
    if (options.process_rule) {
      data.append("process_rule", JSON.stringify(options.process_rule));
    }

    return this.sendRequest(
      routes.updateDocumentByFile.method,
      routes.updateDocumentByFile.url(dataset_id, document_id),
      data,
      null,
      false,
      data.getHeaders(),
    );
  }

  async getDocumentEmbeddingStatus(dataset_id, batch) {
    return this.sendRequest(
      routes.getDocumentEmbeddingStatus.method,
      routes.getDocumentEmbeddingStatus.url(dataset_id, batch),
    );
  }

  async deleteDocument(dataset_id, document_id) {
    return this.sendRequest(
      routes.deleteDocument.method,
      routes.deleteDocument.url(dataset_id, document_id),
    );
  }

  async listDocuments(dataset_id, params) {
    return this.sendRequest(
      routes.listDocuments.method,
      routes.listDocuments.url(dataset_id),
      null,
      params,
    );
  }

  async addDocumentSegment(dataset_id, document_id, options) {
    return this.sendRequest(
      routes.addDocumentSegment.method,
      routes.addDocumentSegment.url(dataset_id, document_id),
      options,
    );
  }

  async getDocumentSegments(dataset_id, document_id, params) {
    return this.sendRequest(
      routes.getDocumentSegments.method,
      routes.getDocumentSegments.url(dataset_id, document_id),
      null,
      params,
    );
  }

  async deleteDocumentSegment(dataset_id, document_id, segment_id) {
    return this.sendRequest(
      routes.deleteDocumentSegment.method,
      routes.deleteDocumentSegment.url(dataset_id, document_id, segment_id),
    );
  }

  async updateDocumentSegment(dataset_id, document_id, segment_id, options) {
    return this.sendRequest(
      routes.updateDocumentSegment.method,
      routes.updateDocumentSegment.url(dataset_id, document_id, segment_id),
      options,
    )
  }
}
