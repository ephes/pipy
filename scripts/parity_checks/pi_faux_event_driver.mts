/**
 * Emit the local Pi reference's authentic session-event stream for a single
 * prompt, deterministically and offline, for the Pi-vs-pipy comparison harness
 * (`automation_pi_comparison.py`).
 *
 * It drives Pi's REAL `AgentSession` (via `createHarness` in
 * `packages/coding-agent/test/test-harness.ts`) with the deterministic faux
 * `streamFn`, subscribes to the `AgentSessionEvent` stream, prompts once, and
 * writes each event as one JSON line to stdout — the same vocabulary Pi's
 * `--mode json` emits, sourced from a real agent run rather than a mock.
 *
 * Usage (run from source the way pi-mono runs its TypeScript):
 *   PI_MONO_DIR=/path/to/pi-mono \
 *     node --import "$PI_MONO_DIR/packages/coding-agent/src/experimental/source-resolver.ts" pi_faux_event_driver.mts <prompt> <reply>
 *
 * It imports the test harness dynamically from $PI_MONO_DIR so this file can
 * live in the pipy repo while resolving against the Pi checkout.
 */

const piMono = process.env.PI_MONO_DIR;
if (!piMono) {
	process.stderr.write("PI_MONO_DIR is not set\n");
	process.exit(2);
}

const prompt = process.argv[2] ?? "ROOT";
const reply = process.argv[3] ?? "SEEN:ROOT";

const harnessUrl = new URL(
	"packages/coding-agent/test/test-harness.ts",
	`file://${piMono.endsWith("/") ? piMono : piMono + "/"}`,
).href;

const { createHarness } = (await import(harnessUrl)) as {
	createHarness: (options: { responses?: string[] }) => Promise<{
		session: { prompt: (text: string) => Promise<void> };
		events: unknown[];
		cleanup: () => void;
	}>;
};

const harness = await createHarness({ responses: [reply] });
try {
	await harness.session.prompt(prompt);
	for (const event of harness.events) {
		process.stdout.write(JSON.stringify(event) + "\n");
	}
} finally {
	harness.cleanup();
}
