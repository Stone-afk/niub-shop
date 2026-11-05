import { proxyJson, serviceUrl } from "@/lib/proxy";

type RouteContext = {
  params: Promise<{ conversationId: string }>;
};

export async function GET(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const { conversationId } = await context.params;
  const baseUrl = process.env.AGENT_SERVICE_URL ?? "http://localhost:8002";
  return proxyJson(
    request,
    serviceUrl(baseUrl, `/conversations/${encodeURIComponent(conversationId)}`),
  );
}

