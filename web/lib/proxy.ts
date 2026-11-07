const UPSTREAM_ERROR = {
  error: {
    code: "upstream_unavailable",
    message: "Upstream service is unavailable",
  },
};

export function serviceUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

function unavailableResponse(): Response {
  return Response.json(UPSTREAM_ERROR, { status: 502 });
}

export async function proxyJson(
  request: Request,
  upstreamUrl: string,
): Promise<Response> {
  const headers: Record<string, string> = {};
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers["content-type"] = contentType;
  }

  const init: RequestInit = {
    method: request.method,
    ...(Object.keys(headers).length > 0 ? { headers } : {}),
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.text();
  }

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, init);
  } catch {
    return unavailableResponse();
  }

  if (upstream.status === 204 || upstream.status === 205) {
    return new Response(null, { status: upstream.status });
  }

  if (!upstream.headers.get("content-type")?.includes("application/json")) {
    return unavailableResponse();
  }

  try {
    return Response.json(await upstream.json(), { status: upstream.status });
  } catch {
    return unavailableResponse();
  }
}

export async function proxyStream(
  request: Request,
  upstreamUrl: string,
): Promise<Response> {
  const headers: Record<string, string> = {};
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers["content-type"] = contentType;
  }

  const init: RequestInit = {
    method: request.method,
    ...(Object.keys(headers).length > 0 ? { headers } : {}),
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.text();
  }

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, init);
  } catch {
    return unavailableResponse();
  }

  const upstreamContentType = upstream.headers.get("content-type");
  if (upstream.ok && !upstreamContentType?.includes("text/event-stream")) {
    return unavailableResponse();
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstreamContentType ? { "content-type": upstreamContentType } : {},
  });
}
