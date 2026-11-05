import { proxyJson, serviceUrl } from "@/lib/proxy";

export function POST(request: Request): Promise<Response> {
  const baseUrl = process.env.AGENT_SERVICE_URL ?? "http://localhost:8002";
  return proxyJson(request, serviceUrl(baseUrl, "/chat"));
}
