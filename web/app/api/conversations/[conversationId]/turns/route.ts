import { proxyStream, serviceUrl } from "@/lib/proxy";

type RouteContext = {
  params: Promise<{ conversationId: string }>;
};

export async function POST(
  request: Request,
  context: RouteContext,
): Promise<Response> {
  const { conversationId } = await context.params;
  const baseUrl = process.env.AGENT_SERVICE_URL ?? "http://localhost:8002";
  return proxyStream(
    request,
    serviceUrl(
      baseUrl,
      `/conversations/${encodeURIComponent(conversationId)}/turns`,
    ),
  );
}

