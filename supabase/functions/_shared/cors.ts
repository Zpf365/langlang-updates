export const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

export function jsonResponse(
  body: Record<string, unknown>,
  status = 200,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

export function normalizeUsername(value: unknown): string {
  return String(value ?? "").trim().slice(0, 64);
}

export function normalizeEmail(value: unknown): string {
  return String(value ?? "").trim().toLowerCase().slice(0, 254);
}

export function validUsername(value: string): boolean {
  return value.length >= 2 && !/[\u0000-\u001f\u007f]/.test(value);
}

export function validEmail(value: string): boolean {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value);
}
