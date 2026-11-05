import { proxyJson, serviceUrl } from "@/lib/proxy";

export function GET(request: Request): Promise<Response> {
  const baseUrl = process.env.MALL_SERVICE_URL ?? "http://localhost:8001";
  return proxyJson(request, serviceUrl(baseUrl, "/products"));
}
