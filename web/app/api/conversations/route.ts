import { proxyJson, serviceUrl } from "@/lib/proxy";

function conversationsUrl(): string {
  const baseUrl = process.env.AGENT_SERVICE_URL ?? "http://localhost:8002";
  return serviceUrl(baseUrl, "/conversations");
}

export function GET(request: Request): Promise<Response> {
  return proxyJson(request, conversationsUrl());
}

export function POST(request: Request): Promise<Response> {
  return proxyJson(request, conversationsUrl());
}

