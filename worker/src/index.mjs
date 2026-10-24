import { agentRoute } from './agent_routes.mjs';
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const result = await agentRoute(request, env, url.pathname);
    return result ?? new Response(JSON.stringify({error:'not_found'}), {
      status:404,headers:{'content-type':'application/json'}
    });
  }
};
