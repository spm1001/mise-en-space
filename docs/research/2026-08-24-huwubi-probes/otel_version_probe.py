# Wire-level probe for mise-huwubi candidates C2/C3/C4/C5, 2026-08-24.
#
# One in-process server+client over memory streams answers four questions:
#   C2  does the default OpenTelemetryMiddleware emit per-call spans with real
#       durations once a TracerProvider is wired (opentelemetry-sdk present)?
#   C3  does instructions= land in the initialize result on the wire?
#   C5  does version= land in serverInfo on the wire?
#   C4  what does a structured-output tool put on the wire (structuredContent
#       vs content blocks)?
# The CC-side halves of C3/C4 (does the MODEL see it?) need a real CC probe —
# see cc_probe/ in this directory.
#
# Run: uv run --with opentelemetry-sdk python docs/research/2026-08-24-huwubi-probes/otel_version_probe.py

import time

import anyio

# --- Wire a real tracer provider BEFORE mcp creates any spans ----------------
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)

from mcp import ClientSession
from mcp.server.mcpserver import MCPServer
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import BaseModel

server = MCPServer(
    "huwubi-probe",
    version="9.9.9-probe",
    instructions="CANARY-INSTRUCTIONS: deposits are pointers; pass base_path.",
)


@server.tool()
def slow_echo(text: str) -> str:
    """Echo after a deliberate 120ms sleep, so the span duration is checkable."""
    time.sleep(0.12)
    return text


class Cues(BaseModel):
    files: list[str]
    open_comment_count: int


@server.tool()
def typed_cues() -> Cues:
    """Returns a typed model — C4: what lands on the wire?"""
    return Cues(files=["content.md"], open_comment_count=3)


async def main() -> None:
    lowlevel = server._lowlevel_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:

            async def run_server() -> None:
                await lowlevel.run(
                    server_read, server_write,
                    lowlevel.create_initialization_options(),
                    raise_exceptions=True,
                )

            tg.start_soon(run_server)
            async with ClientSession(client_read, client_write) as session:
                init = await session.initialize()
                print("C5 serverInfo.version =", repr(init.server_info.version))
                print("C3 instructions       =", repr(init.instructions))

                # C4: tool definition on the wire
                tools = (await session.list_tools()).tools
                for t in tools:
                    print(f"C4 tool={t.name} outputSchema={'yes' if t.output_schema else 'no'}")

                t0 = time.perf_counter()
                r1 = await session.call_tool("slow_echo", {"text": "hello"})
                wall = (time.perf_counter() - t0) * 1000
                print(f"call slow_echo ok={not r1.is_error} wall={wall:.1f}ms")

                r2 = await session.call_tool("typed_cues", {})
                print("C4 structuredContent  =", r2.structured_content)
                print("C4 content blocks     =", [(b.type, getattr(b, 'text', '')[:80]) for b in r2.content])
            tg.cancel_scope.cancel()

    spans = exporter.get_finished_spans()
    print(f"C2 spans captured: {len(spans)}")
    for s in spans:
        dur_ms = (s.end_time - s.start_time) / 1e6
        print(f"  {s.name!r} kind={s.kind.name} dur={dur_ms:.1f}ms attrs={dict(s.attributes)}")


anyio.run(main)
