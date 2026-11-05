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

export interface ChatReply {
  reply: string;
}
