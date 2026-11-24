// Notification sending remains disabled until recipients and provider routing
// are configured. Never acknowledge delivery unless a provider confirms it.
import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
serve((req) => {
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405 })
  }
  return new Response(
    JSON.stringify({ error: "Notification delivery is not configured." }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  )
})
