import asyncio


class Brain:
    """LLM brain via Claude Code in headless mode (`claude -p`).

    Probes for the CLI at startup. If unavailable, falls back to a local
    template brain so agents stay responsive. The dashboard displays which
    mode is active.
    """

    def __init__(self, claude_bin: str = "claude", local_brain=None, local_llm=None) -> None:
        self.claude_bin = claude_bin
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

        # Fast path (voice / typed chat): prefer whichever backend is actually
        # fast on THIS machine. A local Ollama model is only auto-preferred once
        # it has PROVEN quick (last call < 4s); on GPU-less/loaded machines local
        # is slower, so we stay on Claude. "/no_think" suppresses Qwen3 reasoning.
        local_fast = (self.local_llm and self.local_llm.available
                      and self.local_llm.last_latency_ms is not None
                      and self.local_llm.last_latency_ms < 4000)
        if fast and local_fast:
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

    async def _llm_call(self, prompt: str, system: str | None, timeout: float,
                        allow_read: bool = False) -> str:
        args: list[str] = [self.claude_bin, "-p"]
        if allow_read:
            # headless mode blocks the Read tool behind a permission prompt, so
            # image analysis silently fails; bypass lets it read the one local
            # frame we wrote (safe — our own temp file).
            args += ["--dangerously-skip-permissions"]
        if system:
            args += ["--system-prompt", system]
        args.append(prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,   # empty stdin → skip the CLI's 3s stdin wait
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode != 0:
                msg = (stderr.decode(errors="ignore").strip().splitlines() or [""])[-1]
                return f"[brain error: {msg[:200]}]"
            return stdout.decode(errors="ignore").strip() or "[brain returned empty]"
        except FileNotFoundError:
            return "[brain offline: claude CLI not found]"
        except asyncio.TimeoutError:
            return "[brain timeout]"
        except Exception as e:
            return f"[brain error: {e}]"
