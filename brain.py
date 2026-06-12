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
    ) -> str:
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

    async def _llm_call(self, prompt: str, system: str | None, timeout: float) -> str:
        args: list[str] = [self.claude_bin, "-p"]
        if system:
            args += ["--system-prompt", system]
        args.append(prompt)
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
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
