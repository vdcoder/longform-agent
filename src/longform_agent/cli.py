"""Command-line entry point and interactive REPL."""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from pathlib import Path

import openai

from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text

from longform_agent.agent import run_turn
from longform_agent.config import Config
from longform_agent.context import ContextManager
from longform_agent.llm import LLMClient, SYSTEM_PROMPT_BASE
from longform_agent.summarizer import Summarizer
from longform_agent.tools import WebSearch, PatchEditor, ShellRunner

console = Console()

_SLUG_RE = re.compile(r"[^\w\-]")

# Path to the bundled demo project (relative to this file)
_DEMO_CONFIG = Path(__file__).parent.parent.parent / "examples" / "demo_project" / "config.toml"

_BOOK_ART = (
    "      [bold cyan]______ ______[/bold cyan]      \n"
    "    [bold cyan]_/      Y      \\_[/bold cyan]    \n"
    "   [cyan]// ~~ ~~ | ~~ ~  \\\\\\\\[/cyan]   \n"
    "  [cyan]// ~ ~ ~~ | ~~~ ~~ \\\\\\\\[/cyan]  \n"
    " [cyan]//________.|.________\\\\\\\\[/cyan] \n"
    "[bold cyan]`----------`-'----------'[/bold cyan]"
)


def _startup_banner(
    project_path: Path,
    active_chapter: str | None,
    model: str,
    base_url: str,
) -> None:
    """Print the startup banner with ASCII book art and project info."""
    info = Text()
    info.append("\n")
    info.append("LONGFORM  AGENT\n", style="bold white")
    info.append("\u2500" * 15 + "\n", style="dim blue")
    info.append("project   ", style="dim")
    info.append(f"{project_path.name}\n", style="bold")
    info.append("chapter   ", style="dim")
    if active_chapter:
        info.append(f"{active_chapter}\n", style="bold green")
    else:
        info.append("none\n", style="dim italic")
    info.append("model     ", style="dim")
    info.append(f"{model}\n", style="bold")
    info.append("endpoint  ", style="dim")
    info.append(base_url, style="dim")

    console.print(Panel(
        Columns([_BOOK_ART, info], padding=(0, 4)),
        border_style="blue",
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

def _handle_command(cmd: str, ctx: ContextManager, summarizer: Summarizer) -> None:
    parts = cmd.split()
    verb = parts[0].lower()

    if verb == "/help":
        console.print(Panel(
            "[bold]/help[/bold]                    Show this help\n"
            "[bold]/chapter[/bold]                 Show active chapter\n"
            "[bold]/chapter <slug>[/bold]          Switch active chapter\n"
            "[bold]/chapters[/bold]                List all chapters\n"
            "[bold]/new-chapter <slug>[/bold]      Create a new chapter file\n"
            "[bold]/summarize-chapter[/bold]       Summarize active chapter (local LLM)\n"
            "[bold]/memory[/bold]                  Show agent memory\n"
            "[bold]/quit[/bold]                    Exit\n\n"
            '[bold]"""[/bold]                      Start/end multi-line input (paste mode)\n\n'
            "[dim]Agent tools (always require approval): web_search · edit_file · run_shell[/dim]",
            title="Commands",
            border_style="blue",
        ))

    elif verb == "/chapter":
        if len(parts) > 1:
            slug = parts[1]
            ch_path = ctx.project_dir / "chapters" / f"{slug}.md"
            if not ch_path.exists():
                console.print(f"[red]Chapter not found: {slug}[/red]")
                console.print("[dim]Use /chapters to list available chapters.[/dim]")
                return
            # Auto-summarise the outgoing chapter if it has content
            if ctx.active_chapter and ctx.active_chapter != slug:
                full = ctx._load_active_chapter()
                if full.strip():
                    with console.status(
                        f"[bold yellow]Summarizing {ctx.active_chapter}…[/bold yellow]"
                    ):
                        try:
                            summary_text = summarizer.summarize_chapter(
                                full, ctx.active_chapter
                            )
                            sm_path = (
                                ctx.project_dir
                                / "chapters"
                                / f"{ctx.active_chapter}.summary.md"
                            )
                            sm_path.write_text(summary_text, encoding="utf-8")
                            console.print(
                                f"[dim]💾 Summarized [bold]{ctx.active_chapter}[/bold][/dim]"
                            )
                        except Exception as exc:
                            console.print(
                                f"[yellow]⚠ Auto-summarize failed: {exc}[/yellow]"
                            )
            ctx.active_chapter = slug
            console.print(f"[green]✓ Active chapter → [bold]{slug}[/bold][/green]")
        else:
            console.print(
                f"Active chapter: [bold]{ctx.active_chapter or '(none)'}[/bold]"
            )

    elif verb == "/chapters":
        cdir = ctx.project_dir / "chapters"
        if not cdir.exists():
            console.print("[yellow]No chapters directory[/yellow]")
            return
        files = sorted(
            f.stem
            for f in cdir.glob("*.md")
            if not f.name.endswith(".summary.md")
        )
        for f in files:
            ch_path = cdir / f"{f}.md"
            sm_path = cdir / f"{f}.summary.md"
            stale = ""
            if (
                ch_path.exists()
                and sm_path.exists()
                and ch_path.stat().st_mtime > sm_path.stat().st_mtime
            ):
                stale = " [yellow](summary stale)[/yellow]"
            marker = (
                " [bold green]← active[/bold green]" if f == ctx.active_chapter else ""
            )
            console.print(f"  {f}{marker}{stale}")

    elif verb == "/new-chapter":
        if len(parts) < 2:
            console.print(
                "[red]Usage: /new-chapter <slug>  e.g. /new-chapter 03_conclusion[/red]"
            )
            return
        slug = _SLUG_RE.sub("_", parts[1]).strip("_")
        ch_path = ctx.project_dir / "chapters" / f"{slug}.md"
        if ch_path.exists():
            console.print(f"[red]Chapter already exists: {ch_path.name}[/red]")
        else:
            title = slug.split("_", 1)[-1].replace("_", " ").title()
            ch_path.write_text(f"# {title}\n\n", encoding="utf-8")
            console.print(f"[green]✓ Created {ch_path.name}[/green]")
            console.print(f"[dim]Restart with --chapter {slug} to activate it.[/dim]")

    elif verb == "/summarize-chapter":
        if not ctx.active_chapter:
            console.print("[red]No active chapter set (use /chapter <slug>)[/red]")
            return
        full = ctx._load_active_chapter()
        if not full.strip():
            console.print("[red]Active chapter is empty[/red]")
            return
        with console.status("[bold yellow]Summarizing…[/bold yellow]"):
            summary = summarizer.summarize_chapter(full, ctx.active_chapter)
        sm_path = (
            ctx.project_dir / "chapters" / f"{ctx.active_chapter}.summary.md"
        )
        sm_path.write_text(summary, encoding="utf-8")
        console.print(f"[green]✓ Summary saved → {sm_path.name}[/green]")
        console.print(Panel(Markdown(summary), title="Chapter Summary"))

    elif verb == "/memory":
        mem = ctx._load_agent_memory()
        if mem.strip():
            console.print(Panel(Markdown(mem), title="Agent Memory"))
        else:
            console.print("[dim]Agent memory is empty.[/dim]")

    else:
        console.print(f"[red]Unknown command: {verb}. Type /help for help.[/red]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the interactive writing agent REPL."""
    parser = argparse.ArgumentParser(
        description="Longform Agent — interactive writing assistant"
    )
    parser.add_argument(
        "--chapter", "-c",
        help="Active chapter slug (e.g. 02_introduction)",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml in cwd)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with the bundled demo project",
    )
    args = parser.parse_args()

    config_path = str(_DEMO_CONFIG) if args.demo else args.config
    config = Config.load(config_path)

    # When running demo, resolve project_dir relative to the demo config file
    if args.demo:
        demo_dir = _DEMO_CONFIG.parent
        config.agent.project_dir = str(demo_dir / config.agent.project_dir)

    ctx = ContextManager(config, config.agent.project_dir, args.chapter)
    llm = LLMClient(config)
    summarizer = Summarizer(llm)
    search = WebSearch(config)
    editor = PatchEditor(config.agent.project_dir)
    shell = ShellRunner(Path(config.agent.project_dir).parent)

    project_path = Path(config.agent.project_dir)
    (project_path / "chapters").mkdir(parents=True, exist_ok=True)

    memory_path = project_path / "agent_memory.md"
    if not memory_path.exists():
        memory_path.write_text(
            "# Agent Memory\n\n"
            "<!-- The assistant updates this file to remember key facts, "
            "decisions, and context. -->\n",
            encoding="utf-8",
        )

    _startup_banner(project_path, ctx.active_chapter, config.llm.model, config.llm.base_url)

    if not llm.health_check():
        console.print(
            "[yellow]⚠ LLM not reachable — is your server running on "
            f"{config.llm.base_url}?[/yellow]"
        )

    if args.chapter and ctx.is_chapter_summary_stale():
        console.print(
            "[yellow]⚠ Chapter summary may be stale — run /summarize-chapter[/yellow]"
        )

    console.print(
        '[dim]Type /help for commands · """ for multi-line · Ctrl+C or /quit to exit[/dim]\n'
    )

    history = ctx.load_conversation_history()
    summary = ctx.load_conversation_summary()
    keep = config.agent.keep_last_n * 2

    while True:
        try:
            user_input = Prompt.ask("\n[bold green]you ❯[/bold green]").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input:
            continue

        # Multi-line mode: open with """ and close with """
        if user_input == '"""':
            console.print(
                '[dim]Multi-line mode — paste your text, '
                'then type [bold]"""[/bold] on its own line to send[/dim]'
            )
            lines: list[str] = []
            try:
                while True:
                    line = input()
                    if line.strip() == '"""':
                        break
                    lines.append(line)
            except (KeyboardInterrupt, EOFError):
                console.print("[yellow]Multi-line input cancelled[/yellow]")
                continue
            user_input = "\n".join(lines).strip()
            if not user_input:
                continue

        if user_input.lower() in ("/quit", "/exit", "/q"):
            break
        if user_input.startswith("/"):
            _handle_command(user_input, ctx, summarizer)
            continue

        system = ctx.build_system_prompt(SYSTEM_PROMPT_BASE)
        messages = ctx.build_messages(history, summary, user_input)

        try:
            _, final_text = run_turn(
                llm,
                messages,
                system,
                search,
                editor,
                shell,
                project_dir=project_path,
                memory_max_chars=config.agent.memory_max_chars,
                max_tool_iterations=config.agent.max_tool_iterations,
            )
        except openai.APIConnectionError:
            console.print(
                f"[red]Cannot reach the LLM at [bold]{config.llm.base_url}[/bold]. "
                "Is your server running? Check base_url in config.toml.[/red]"
            )
            continue
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
            continue

        history.append({"role": "user", "content": user_input})
        if final_text:
            history.append({"role": "assistant", "content": final_text})

        # Sliding window: fold evicted exchanges into the rolling summary
        while len(history) > keep:
            evicted: list[dict] = []
            if history[0]["role"] == "user":
                evicted.append(history.pop(0))
            if history and history[0]["role"] == "assistant":
                evicted.append(history.pop(0))
            if evicted:
                try:
                    summary = summarizer.summarize_conversation(
                        evicted, existing_summary=summary
                    )
                    if len(summary) > config.agent.summary_max_chars:
                        tail = summary[-config.agent.summary_max_chars:]
                        nl = tail.find("\n")
                        summary = tail[nl + 1:] if nl != -1 else tail
                except Exception as exc:
                    console.print(
                        f"[dim yellow]Auto-summarize failed: {exc}[/dim yellow]"
                    )

        ctx.save_conversation_history(history[-keep:])
        ctx.save_conversation_summary(summary)

    console.print("\n[yellow]Goodbye![/yellow]")
    ctx.save_conversation_history(history[-keep:])
    ctx.save_conversation_summary(summary)


if __name__ == "__main__":
    main()
