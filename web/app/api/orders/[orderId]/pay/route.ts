import { proxyJson, serviceUrl } from "@/lib/proxy";

function payUrl(orderId: string): string {
  const baseUrl = process.env.MALL_SERVICE_URL ?? "http://localhost:8001";
  return serviceUrl(baseUrl, `/orders/${encodeURIComponent(orderId)}/pay`);
}

export function POST(
  request: Request,
  context: { params: Promise<{ orderId: string }> },
): Promise<Response> {
  return context.params.then(({ orderId }) => proxyJson(request, payUrl(orderId)));
}
