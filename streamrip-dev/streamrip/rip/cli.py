"""Streamlined CLI with integrated SSE server support."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from functools import wraps
from typing import Any

import aiofiles
import aiohttp
import click
from click_help_colors import HelpColorsGroup
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.prompt import Confirm
from rich.traceback import install

from .. import __version__, db
from ..config import DEFAULT_CONFIG_PATH, Config, OutdatedConfigError, set_user_defaults
from ..console import console
from ..utils.ssl_utils import get_aiohttp_connector_kwargs
from .main import Main

# SSE server integration
try:
    from ..sse_server import SSEServer
    SSE_AVAILABLE = True
except ImportError:
    SSE_AVAILABLE = False
    SSEServer = None


def coro(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


async def run_with_sse(main_coro, sse_port: int, sse_host: str):
    """Run main task with optional SSE server."""
    if sse_port == 0 or not SSE_AVAILABLE:
        if sse_port != 0 and not SSE_AVAILABLE:
            console.print("[yellow]SSE not available. Install: pip install fastapi uvicorn[/yellow]")
        await main_coro
        return

    # Start SSE server
    sse_server = SSEServer(host=sse_host, port=sse_port)
    console.print(f"[green]SSE server starting on http://{sse_host}:{sse_port}[/green]")
    console.print(f"[cyan]Events endpoint: http://{sse_host}:{sse_port}/events[/cyan]")
    console.print(f"[cyan]Web interface: http://{sse_host}:{sse_port}/[/cyan]")
    console.print(f"[yellow]Open the web interface in your browser to monitor progress[/yellow]")
    
    server_task = sse_server.start_background()
    
    try:
        await asyncio.sleep(0.5)  # Let server start
        await main_coro
        await asyncio.sleep(1.0)  # Let final events send
    finally:
        if not server_task.done():
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass


@click.group(cls=HelpColorsGroup, help_headers_color="yellow", help_options_color="green")
@click.version_option(version=__version__)
@click.option("--config-path", default=DEFAULT_CONFIG_PATH, 
              help="Path to config file", type=click.Path(readable=True, writable=True))
@click.option("-f", "--folder", help="Download folder", 
              type=click.Path(file_okay=False, dir_okay=True))
@click.option("-ndb", "--no-db", help="Skip database checks", is_flag=True)
@click.option("-q", "--quality", help="Max quality (0-4)", type=click.IntRange(0, 4))
@click.option("-c", "--codec", help="Convert to codec (ALAC, FLAC, MP3, AAC, OGG)")
@click.option("--no-progress", help="Disable progress bars", is_flag=True)
@click.option("--no-ssl-verify", help="Disable SSL verification", is_flag=True)
@click.option("--sse-port", help="SSE server port (0=disable)", type=int, default=8000)
@click.option("--sse-host", help="SSE server host", default="127.0.0.1")
@click.option("-v", "--verbose", help="Debug mode", is_flag=True)
@click.pass_context
def rip(ctx, config_path, folder, no_db, quality, codec, no_progress, 
        no_ssl_verify, sse_port, sse_host, verbose):
    """Streamrip: the all-in-one music downloader."""
    
    # Setup logging
    logging.basicConfig(level="INFO", format="%(message)s", handlers=[RichHandler()])
    logger = logging.getLogger("streamrip")
    
    if verbose:
        install(console=console, suppress=[click], show_locals=True, locals_hide_sunder=False)
        logger.setLevel(logging.DEBUG)
    else:
        install(console=console, suppress=[click, asyncio], max_frames=1)

    # Create config if needed
    if not os.path.isfile(config_path):
        console.print(f"Creating config at [cyan]{config_path}[/cyan]")
        set_user_defaults(config_path)

    # Store context
    ctx.ensure_object(dict)
    ctx.obj.update({
        "config_path": config_path,
        "sse_port": sse_port,
        "sse_host": sse_host
    })

    # Load and validate config
    try:
        c = Config(config_path)
    except OutdatedConfigError as e:
        console.print(f"{e}\nAuto-updating config...")
        Config.update_file(config_path)
        c = Config(config_path)
    except Exception as e:
        console.print(f"[red]Config error: {e}[/red]\nTry: rip config reset")
        ctx.obj["config"] = None
        return

    # Apply CLI overrides
    if no_db:
        c.session.database.downloads_enabled = False
    if folder:
        c.session.downloads.folder = folder
    if quality is not None:
        for source in ["qobuz", "tidal", "deezer", "soundcloud"]:
            setattr(c.session, source).quality = quality
    if codec:
        c.session.conversion.enabled = True
        c.session.conversion.codec = codec.upper()
    if no_progress:
        c.session.cli.progress_bars = False
    if no_ssl_verify:
        c.session.downloads.verify_ssl = False

    ctx.obj["config"] = c


@rip.command()
@click.argument("urls", nargs=-1, required=True)
@click.pass_context
@coro
async def url(ctx, urls):
    """Download from URLs."""
    if not ctx.obj.get("config"):
        return

    async def download_task():
        try:
            with ctx.obj["config"] as cfg:
                # Check for updates if enabled
                if cfg.session.misc.check_for_updates:
                    version_task = asyncio.create_task(
                        latest_streamrip_version(cfg.session.downloads.verify_ssl)
                    )
                else:
                    version_task = None

                # Main download
                async with Main(cfg) as main:
                    await main.add_all(urls)
                    await main.resolve()
                    await main.rip()

                # Show version info
                if version_task:
                    latest_version, notes = await version_task
                    if latest_version != __version__:
                        console.print(f"\n[green]New version available: [cyan]v{latest_version}[/cyan][/green]")
                        console.print("[white]pip3 install streamrip --upgrade[/white]")
                        if notes:
                            console.print(Markdown(notes))

        except aiohttp.ClientConnectorCertificateError as e:
            from ..utils.ssl_utils import print_ssl_error_help
            console.print(f"[red]SSL error: {e}[/red]")
            print_ssl_error_help()

    await run_with_sse(download_task(), ctx.obj["sse_port"], ctx.obj["sse_host"])


@rip.command()
@click.argument("path", type=click.Path(exists=True, readable=True, file_okay=True))
@click.pass_context
@coro
async def file(ctx, path):
    """Download from file (URLs or JSON)."""
    
    async def download_task():
        try:
            with ctx.obj["config"] as cfg:
                async with Main(cfg) as main:
                    async with aiofiles.open(path, "r") as f:
                        content = await f.read()
                    
                    # Try JSON format first
                    try:
                        items = json.loads(content)
                        console.print(f"JSON file: [yellow]{len(items)}[/yellow] items")
                        await main.add_all_by_id(
                            [(i["source"], i["media_type"], i["id"]) for i in items]
                        )
                    except json.JSONDecodeError:
                        # Plain URL list
                        urls = content.strip().split()
                        unique_urls = list(set(urls))
                        if len(unique_urls) < len(urls):
                            console.print(f"[orange]{len(urls) - len(unique_urls)}[/orange] duplicate URLs removed")
                        console.print(f"URL list: [yellow]{len(unique_urls)}[/yellow] items")
                        await main.add_all(unique_urls)

                    await main.resolve()
                    await main.rip()
                    
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    await run_with_sse(download_task(), ctx.obj["sse_port"], ctx.obj["sse_host"])


@rip.command()
@click.option("-s", "--source", help="Search source")
@click.option("-fs", "--fallback-source", help="Fallback search source")
@click.argument("url", required=True)
@click.pass_context
@coro
async def lastfm(ctx, source, fallback_source, url):
    """Download Last.fm playlist."""
    config = ctx.obj["config"]
    if source:
        config.session.lastfm.source = source
    if fallback_source:
        config.session.lastfm.fallback_source = fallback_source

    async def download_task():
        with config as cfg:
            async with Main(cfg) as main:
                await main.resolve_lastfm(url)
                await main.rip()

    await run_with_sse(download_task(), ctx.obj["sse_port"], ctx.obj["sse_host"])


@rip.command()
@click.option("-f", "--first", help="Download first result", is_flag=True)
@click.option("-o", "--output-file", help="Save results to file", type=click.Path(writable=True))
@click.option("-n", "--num-results", help="Max results", default=100, type=click.IntRange(min=1))
@click.argument("source", required=True)
@click.argument("media-type", required=True)
@click.argument("query", required=True)
@click.pass_context
@coro
async def search(ctx, first, output_file, num_results, source, media_type, query):
    """Search and download."""
    if first and output_file:
        console.print("[red]Cannot use both --first and --output-file[/red]")
        return

    async def search_task():
        with ctx.obj["config"] as cfg:
            async with Main(cfg) as main:
                if first:
                    await main.search_take_first(source, media_type, query)
                elif output_file:
                    await main.search_output_file(source, media_type, query, output_file, num_results)
                else:
                    await main.search_interactive(source, media_type, query)
                await main.resolve()
                await main.rip()

    await run_with_sse(search_task(), ctx.obj["sse_port"], ctx.obj["sse_host"])


@rip.command()
@click.argument("source")
@click.argument("media-type")
@click.argument("id")
@click.pass_context
@coro
async def id(ctx, source, media_type, id):
    """Download by ID."""
    async def download_task():
        with ctx.obj["config"] as cfg:
            async with Main(cfg) as main:
                await main.add_by_id(source, media_type, id)
                await main.resolve()
                await main.rip()

    await run_with_sse(download_task(), ctx.obj["sse_port"], ctx.obj["sse_host"])


@rip.command()
@click.option("--port", type=int, default=8000, help="Server port")
@click.option("--host", default="127.0.0.1", help="Server host")
@coro
async def server(host, port):
    """Start standalone SSE server."""
    if not SSE_AVAILABLE:
        console.print("[red]SSE server not available. Install: pip install fastapi uvicorn[/red]")
        return

    sse_server = SSEServer(host=host, port=port)
    console.print(f"[green]SSE server: http://{host}:{port}[/green]")
    console.print(f"[cyan]Events: http://{host}:{port}/events[/cyan]")
    console.print("[yellow]Press Ctrl+C to stop[/yellow]")
    
    try:
        await sse_server.start()
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")


# Config commands
@rip.group()
def config():
    """Manage config files."""
    pass


@config.command("open")
@click.option("-v", "--vim", help="Open in vim", is_flag=True)
@click.pass_context
def config_open(ctx, vim):
    """Open config file."""
    config_path = ctx.obj["config_path"]
    console.print(f"Opening [cyan]{config_path}[/cyan]")
    
    if vim:
        editor = "nvim" if shutil.which("nvim") else "vim" if shutil.which("vim") else None
        if editor:
            subprocess.run([editor, config_path])
        else:
            console.print("[yellow]Vim not found, using default editor[/yellow]")
            click.launch(config_path)
    else:
        click.launch(config_path)


@config.command("reset")
@click.option("-y", "--yes", help="Skip confirmation", is_flag=True)
@click.pass_context
def config_reset(ctx, yes):
    """Reset config file."""
    config_path = ctx.obj["config_path"]
    if not yes and not Confirm.ask(f"Reset config at {config_path}?"):
        console.print("[green]Cancelled[/green]")
        return
    
    set_user_defaults(config_path)
    console.print(f"[green]Config reset: [cyan]{config_path}[/cyan][/green]")


@config.command("path")
@click.pass_context
def config_path(ctx):
    """Show config path."""
    console.print(f"Config: [cyan]{ctx.obj['config_path']}[/cyan]")


# Database commands
@rip.group()
def database():
    """Manage databases."""
    pass


@database.command("browse")
@click.argument("table", type=click.Choice(["downloads", "failed"], case_sensitive=False))
@click.pass_context
def database_browse(ctx, table):
    """Browse database contents."""
    from rich.table import Table
    
    cfg = ctx.obj["config"]
    if not cfg:
        return
    
    if table.lower() == "downloads":
        downloads_db = db.Downloads(cfg.session.database.downloads_path)
        t = Table(title="Downloads Database")
        t.add_column("Row")
        t.add_column("ID")
        for i, row in enumerate(downloads_db.all()):
            t.add_row(f"{i:02}", *row)
        console.print(t)
    else:
        failed_db = db.Failed(cfg.session.database.failed_downloads_path)
        t = Table(title="Failed Downloads Database")
        t.add_column("Source")
        t.add_column("Media Type") 
        t.add_column("ID")
        for i, row in enumerate(failed_db.all()):
            t.add_row(f"{i:02}", *row)
        console.print(t)


async def latest_streamrip_version(verify_ssl: bool = True) -> tuple[str, str | None]:
    """Get latest version from PyPI."""
    connector_kwargs = get_aiohttp_connector_kwargs(verify_ssl=verify_ssl)
    connector = aiohttp.TCPConnector(**connector_kwargs)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Get version from PyPI
        async with session.get("https://pypi.org/pypi/streamrip/json") as resp:
            data = await resp.json()
        version = data["info"]["version"]

        if version == __version__:
            return version, None

        # Get release notes from GitHub
        async with session.get("https://api.github.com/repos/nathom/streamrip/releases/latest") as resp:
            data = await resp.json()
        notes = data.get("body", "")
        
    return version, notes


if __name__ == "__main__":
    rip()