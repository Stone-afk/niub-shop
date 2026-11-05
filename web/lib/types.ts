export interface Product {
  id: string;
  name: string;
  description: string;
  price_cents: number;
  currency: string;
}

export interface ProductList {
  items: Product[];
}

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export type TurnStatus = "pending" | "completed" | "failed";

export interface ConversationTurn {
  id: string;
  user_content: string;
  assistant_content: string | null;
  status: TurnStatus;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_turn_status: TurnStatus | null;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turns: ConversationTurn[];
}
