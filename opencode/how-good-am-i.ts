// how-good-am-i: injects Artificial Analysis benchmark scores for the model in use into OpenCode's
// system prompt. https://github.com/AaronBergman/how-good-am-i
//
// One file for both plugin APIs: OpenCode 1.x reads `server` (hooks), OpenCode 2.x reads `setup`.
// The text comes from ~/.how-good-am-i/hgai.py and is cached per model+variant, so it is computed
// once per process and stays byte-stable (keeps prompt caching intact). Never throws: an error in
// a hook would break the request, so every hook body is wrapped.
import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const HGAI = join(process.env.HGAI_HOME || join(homedir(), ".how-good-am-i"), "hgai.py")
const cache = new Map<string, string>()
const variantBySession = new Map<string, string | undefined>()

function block(model: string, variant?: string): string {
  const key = `${model}#${variant ?? ""}`
  const hit = cache.get(key)
  if (hit !== undefined) return hit
  let text = ""
  try {
    if (existsSync(HGAI)) {
      const r = spawnSync("python3", [HGAI, "opencode"], {
        input: JSON.stringify({ model, variant: variant ?? null }),
        encoding: "utf8",
        timeout: 10000,
      })
      if (r.status === 0) text = (r.stdout || "").trim()
    }
  } catch {}
  cache.set(key, text)
  return text
}

// OpenCode 1.x
const server = async () => ({
  "chat.message": async (input: any, output: any) => {
    try {
      variantBySession.set(input.sessionID, output?.message?.model?.variant ?? input.variant)
    } catch {}
  },
  "experimental.chat.system.transform": async (input: any, output: any) => {
    try {
      if (String(output.system?.[0] ?? "").startsWith("You are a title generator")) return
      const m = input.model || {}
      let v = input.sessionID ? variantBySession.get(input.sessionID) : undefined
      if (v && !(m.variants && m.variants[v])) v = undefined // 1.x passes unknown variants through
      const text = block(`${m.providerID}/${m.api?.id || m.id}`, v)
      if (text) output.system.push(text)
    } catch {}
  },
})

// OpenCode 2.x
const setup = async (ctx: any) => {
  await ctx.session.hook("context", async (event: any) => {
    try {
      const m = event.model || {}
      const text = block(`${m.providerID}/${m.id}`, m.variant)
      if (text) event.system.push({ type: "text", text })
    } catch {}
  })
}

export default { id: "how-good-am-i", server, setup }
