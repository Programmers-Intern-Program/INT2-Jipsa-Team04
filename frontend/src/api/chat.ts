import { apiFetch } from "./client";

export interface ConversationResponse {
    id: number;
    title: string;
    createdAt: string;
    lastActivityAt: string;
}

export interface Citation {
    sourceId: string | null;
    fileId: number;
    fileName: string;
    page: number | null;
    sectionTitle: string | null;
    excerpt: string | null;
    score: number | null;
}

export interface ReferenceFile {
    fileId: number;
    fileName: string | null;
}

export interface ChatMessageResponse {
    messageId: number;
    question: string;
    answer: string;
    status: "answered" | "insufficient_evidence";
    feedbackRating: "UP" | "DOWN" | null;
    feedbackComment: string | null;
    feedbackAt: string | null;
    createdAt: string;
    citations: Citation[];
    referenceFiles: ReferenceFile[];
}

export interface SendMessageRequest {
    question: string;
    fileIds: number[];
    topK?: number;
    scoreThreshold?: number;
}

export function listConversations(): Promise<ConversationResponse[]> {
    return apiFetch<ConversationResponse[]>("/conversations");
}

export function createConversation(title?: string): Promise<ConversationResponse> {
    return apiFetch<ConversationResponse>("/conversations", {
        method: "POST",
        body: { title },
    });
}

export function getConversation(id: number): Promise<ConversationResponse> {
    return apiFetch<ConversationResponse>(`/conversations/${id}`);
}

export function renameConversation(id: number, title: string): Promise<void> {
    return apiFetch<{ success: boolean }>(`/conversations/${id}`, {
        method: "PATCH",
        body: { title },
    }).then(() => undefined);
}

export function deleteConversation(id: number): Promise<void> {
    return apiFetch<{ success: boolean }>(`/conversations/${id}`, {
        method: "DELETE",
    }).then(() => undefined);
}

export function listMessages(conversationId: number): Promise<ChatMessageResponse[]> {
    return apiFetch<ChatMessageResponse[]>(`/conversations/${conversationId}/messages`);
}

export function sendMessage(
    conversationId: number,
    request: SendMessageRequest
): Promise<ChatMessageResponse> {
    return apiFetch<ChatMessageResponse>(`/conversations/${conversationId}/messages`, {
        method: "POST",
        body: request,
    });
}

export function submitFeedback(
    conversationId: number,
    messageId: number,
    rating: "UP" | "DOWN",
    comment?: string
): Promise<void> {
    return apiFetch<{ success: boolean }>(
        `/conversations/${conversationId}/messages/${messageId}/feedback`,
        {
            method: "PATCH",
            body: { rating, comment },
        }
    ).then(() => undefined);
}