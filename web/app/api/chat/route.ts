import { proxyStream, serviceUrl } from "@/lib/proxy";

export function POST(request: Request): Promise<Response> {
  const baseUrl = process.env.AGENT_SERVICE_URL ?? "http://localhost:8002";
  return proxyStream(request, serviceUrl(baseUrl, "/chat"));
}
