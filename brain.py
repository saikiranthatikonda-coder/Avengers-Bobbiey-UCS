import asyncio
import shutil
import tempfile
from pathlib import Path


def resolve_claude_bin(name: str = "claude") -> str:
    """Find the Claude CLI. An explicit path or a PATH hit wins; otherwise
    check the native installer's location (~/.local/bin), which the installer
    does not always add to PATH on Windows."""
    if shutil.which(name):
        return name
    if Path(name).name.lower() in ("claude", "claude.exe"):
        for cand in (Path.home() / ".local" / "bin" / "claude.exe",
                     Path.home() / ".local" / "bin" / "claude"):
            if cand.is_file():
                return str(cand)
    return name


# The CLI loads CLAUDE.md / project settings from its working directory. The
# server runs from the repo, whose CLAUDE.md is the *developer* manual — it must
# never leak into operator-facing agent replies, so calls run from a neutral dir.
BRAIN_CWD = Path(tempfile.gettempdir()) / "bobbiey-ucs-brain"


class Brain:
    """LLM brain via Claude Code in headless mode (`claude -p`).

    Probes for the CLI at startup. If unavailable, falls back to a local
    template brain so agents stay responsive. The dashboard displays which
    mode is active.
    """

    def __init__(self, claude_bin: str = "claude", local_brain=None, local_llm=None) -> None:
        self.claude_bin = resolve_claude_bin(claude_bin)
        self.local = local_brain
        self.local_llm = local_llm  # LocalLLM (OpenAI-compatible endpoint) or None
        self.mode: str = "unknown"  # "llm" | "local-llm" | "local" | "unknown"
        self.force_local = False    # user picked a local model → route replies to it

    async def probe(self) -> str:
        """Resolve brain mode: claude CLI → local LLM endpoint → templates."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.claude_bin, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            if proc.returncode == 0 and stdout:
                self.mode = "llm"
                # still probe the local endpoint so the insights engine can use it
                if self.local_llm:
                    await self.local_llm.probe()
                return self.mode
        except Exception:
            pass
        if self.local_llm and await self.local_llm.probe():
            self.mode = "local-llm"
            return self.mode
        self.mode = "local" if self.local else "offline"
        return self.mode

    async def think(
        self,
        prompt: str,
        system: str | None = None,
        agent: str = "jarvis",
        timeout: float = 120.0,
        fast: bool = False,
    ) -> str:
        # Forced-local: the operator explicitly picked a local model in the UI,
        # so ALL replies route through it (falls back to Claude only if it errors).
        if self.force_local and self.local_llm and self.local_llm.available:
            alt = await self.local_llm.chat(prompt + " /no_think", system=system,
                                            timeout=timeout, max_tokens=220)
            if alt:
                return alt
            # local failed — fall through to Claude/templates below

        # Fast path (voice / typed chat) — only when Claude is NOT the active
        # brain. Operator policy (2026-09-28): Claude first whenever reachable;
        # latency-based switching made replies flip between Claude and a small
        # local model depending on the last insights call. Local still covers
        # insights and takes over if a Claude call fails (below).
        # "/no_think" suppresses Qwen3 reasoning.
        local_fast = (self.local_llm and self.local_llm.available
                      and self.local_llm.last_latency_ms is not None
                      and self.local_llm.last_latency_ms < 4000)
        if fast and local_fast and self.mode != "llm":
            alt = await self.local_llm.chat(prompt + " /no_think", system=system,
                                            timeout=min(timeout, 20), max_tokens=180)
            if alt:
                return alt
        if self.mode == "llm":
            reply = await self._llm_call(prompt, system, timeout)
            if reply.startswith("[brain"):
                # claude failed at runtime — try local LLM, then templates
                if self.local_llm and self.local_llm.available:
                    alt = await self.local_llm.chat(prompt, system=system, timeout=timeout)
                    if alt:
                        return alt
                if self.local:
                    return self.local.for_agent(agent, prompt)
            return reply
        if self.mode == "local-llm" and self.local_llm:
            reply = await self.local_llm.chat(prompt, system=system, timeout=timeout)
            if reply:
                return reply
            if self.local:
                return self.local.for_agent(agent, prompt)
            return "[brain unavailable]"
        # local / offline
        if self.local:
            return self.local.for_agent(agent, prompt)
        return "[brain unavailable]"

    async def see(self, image_path: str, prompt: str, timeout: float = 60.0) -> str:
        """Vision via the Claude CLI. Claude Code views local images with its
        Read tool, but in headless -p mode it only does so if explicitly told —
        so we command the Read up front and allow the tool. Retries once if the
        model claims it got no image."""
        full = (
            f"Use your Read tool to open the image file at this exact path: {image_path}\n"
            "Look at the actual image contents, then answer:\n" + prompt
        )
        for attempt in range(2):
            reply = await self._llm_call(full, system=None, timeout=timeout,
                                         allow_read=True)
            low = (reply or "").lower()
            if reply and not reply.startswith("[") and not any(
                s in low for s in ("no image", "not attached", "cannot read",
                                    "couldn't", "could not open", "unable to",
                                    "no file", "wasn't attached", "was not attached")):
                return reply
        return reply

    def _cli_args(self, prompt: str, system: str | None, allow_read: bool) -> list[str]:
        args: list[str] = [self.claude_bin, "-p"]
        if allow_read:
            # headless mode blocks the Read tool behind a permission prompt, so
            # image analysis silently fails; bypass lets it read the one local
            # frame we wrote. --tools Read keeps the bypass to that one tool.
            args += ["--tools", "Read", "--dangerously-skip-permissions"]
        else:
            # text replies are pure reasoning over the context we pass in; with
            # tools on, the CLI tries (blocked) shell commands and stalls ~30s.
            args += ["--tools", ""]
        # stream-json: tokens arrive as they're generated (first token ~1.8 s vs
        # a 4.8 s full run) and we can stop reading at message end — the CLI
        # spends ~2 s on post-turn housekeeping after the answer is complete.
        # --strict-mcp-config: skip the operator's claude.ai MCP connectors,
        # which the CLI otherwise starts on every call (~0.3 s, no use here).
        args += ["--output-format", "stream-json", "--include-partial-messages",
                 "--verbose", "--strict-mcp-config"]
        if system:
            args += ["--system-prompt", system]
        args += ["--", prompt]   # "--" ends the variadic --tools list
        return args

    async def _claude_stream(self, prompt: str, system: str | None, timeout: float,
                             allow_read: bool = False, on_delta=None) -> str:
        """Run the CLI with stream-json. Calls on_delta(text) per token chunk.
        Text-only calls return at message_stop (the answer is complete there);
        tool calls (vision Read) wait for the final `result` event. Returns the
        same strings as before, including "[brain …]" error markers."""
        import json as _json
        args = self._cli_args(prompt, system, allow_read)
        proc = None
        try:
            BRAIN_CWD.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,   # empty stdin → skip the CLI's 3s stdin wait
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(BRAIN_CWD),                 # keep dev CLAUDE.md out of agent context
                limit=1 << 20,                      # stream-json lines can be long
            )
            text, final, done = [], None, False

            async def _read():
                nonlocal final, done
                async for raw in proc.stdout:
                    try:
                        ev = _json.loads(raw)
                    except Exception:
                        continue
                    kind = ev.get("type")
                    if kind == "stream_event":
                        e = ev.get("event") or {}
                        d = e.get("delta") or {}
                        if d.get("type") == "text_delta" and d.get("text"):
                            text.append(d["text"])
                            if on_delta:
                                await on_delta(d["text"])
                        elif e.get("type") == "message_stop" and not allow_read and text:
                            done = True
                            return
                    elif kind == "result":
                        if ev.get("is_error"):
                            final = f"[brain error: {str(ev.get('result') or ev.get('subtype'))[:200]}]"
                        else:
                            final = ev.get("result")
                        done = True
                        return

            await asyncio.wait_for(_read(), timeout=timeout)
            if final is None and not done:
                await proc.wait()
                if proc.returncode not in (0, None):
                    err = (await proc.stderr.read()).decode(errors="ignore").strip().splitlines()
                    return f"[brain error: {(err or [''])[-1][:200]}]"
            out = (final if isinstance(final, str) and final.strip() else "".join(text)).strip()
            return out or "[brain returned empty]"
        except FileNotFoundError:
            return "[brain offline: claude CLI not found]"
        except asyncio.TimeoutError:
            return "[brain timeout]"
        except asyncio.CancelledError:
            raise                                   # cancellation is the caller's decision
        except Exception as e:
            return f"[brain error: {e}]"
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()                     # stop the post-answer housekeeping
                except ProcessLookupError:
                    pass

    async def _llm_call(self, prompt: str, system: str | None, timeout: float,
                        allow_read: bool = False) -> str:
        return await self._claude_stream(prompt, system, timeout, allow_read=allow_read)

    # ── streaming entry point (real-time engine) ──────────────────
    async def stream(self, prompt: str, system: str | None = None, agent: str = "jarvis",
                     on_delta=None, timeout: float = 90.0) -> tuple[str, str]:
        """Like think(), but pushes text as it is generated. Returns (reply,
        path) with path in {"claude", "local-llm", "template"}. Same provider
        policy as think(): operator-forced local first, then Claude, then the
        local LLM, then rule templates — so nothing ever goes silent."""
        async def _emit_all(txt):
            if on_delta and txt:
                await on_delta(txt)

        llm = self.local_llm
        local_ok = bool(llm and llm.available)
        if self.force_local and local_ok:
            r = await llm.chat_stream(prompt, system=system, on_delta=on_delta, timeout=timeout)
            if r:
                return r, "local-llm"
        if self.mode == "llm":
            r = await self._claude_stream(prompt, system, timeout, on_delta=on_delta)
            if not r.startswith("[brain"):
                return r, "claude"
        if local_ok:
            r = await llm.chat_stream(prompt, system=system, on_delta=on_delta, timeout=timeout)
            if r:
                return r, "local-llm"
        if self.local:
            r = self.local.for_agent(agent, prompt)
            await _emit_all(r)
            return r, "template"
        return "[brain offline]", "offline"
