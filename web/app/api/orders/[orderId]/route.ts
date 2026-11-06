import { proxyJson, serviceUrl } from "@/lib/proxy";

function orderUrl(orderId: string): string {
  const baseUrl = process.env.MALL_SERVICE_URL ?? "http://localhost:8001";
  return serviceUrl(
    baseUrl,
    `/orders/${encodeURIComponent(orderId)}/preview`,
  );
}

export function GET(
  request: Request,
  context: { params: Promise<{ orderId: string }> },
): Promise<Response> {
  return context.params.then(({ orderId }) => proxyJson(request, orderUrl(orderId)));
}
