/**
 * Drive the local Pi reference's REAL SessionManager through a canonical
 * session-tree workflow and emit a normalized structure as one JSON line, for
 * the Pi-vs-pipy session-tree comparison harness
 * (`session_tree_pi_comparison.py`).
 *
 * The scenario (mirrored exactly on the pipy side):
 *   create -> user ROOT -> user MAIN -> branch back to ROOT -> user ALT
 *   -> name "compare-tree" -> fork the active (ALT) branch.
 *
 * It emits the observable semantics only (no volatile ids/timestamps/paths):
 * the session name, the set of root->leaf user-message chains, the active
 * branch users when the ALT vs MAIN leaf is selected, whether the fork records
 * a parent and carries the ALT chain, and the durable reopen leaf chain.
 *
 * Usage (run with pi-mono's own tsx so workspace deps resolve):
 *   PI_MONO_DIR=/path/to/pi-mono \
 *     "$PI_MONO_DIR/node_modules/.bin/tsx" pi_session_tree_driver.mts <sessionDir> <cwd> <forkCwd>
 */

const piMono = process.env.PI_MONO_DIR;
if (!piMono) {
	process.stderr.write("PI_MONO_DIR is not set\n");
	process.exit(2);
}

const sessionDir = process.argv[2];
const cwd = process.argv[3];
const forkCwd = process.argv[4];

const base = `file://${piMono.endsWith("/") ? piMono : piMono + "/"}`;
const smUrl = new URL(
	"packages/coding-agent/src/core/session-manager.ts",
	base,
).href;
const utilUrl = new URL(
	"packages/coding-agent/test/utilities.ts",
	base,
).href;

const { SessionManager } = (await import(smUrl)) as {
	SessionManager: any;
};
const { userMsg, assistantMsg } = (await import(utilUrl)) as {
	userMsg: (text: string) => any;
	assistantMsg: (text: string) => any;
};

function userText(message: any): string {
	const content = message?.content;
	if (typeof content === "string") return content;
	if (Array.isArray(content)) {
		return content.map((c: any) => c?.text ?? "").join("");
	}
	return "";
}

function userChain(mgr: any, leafId: string): string[] {
	return mgr
		.getBranch(leafId)
		.filter((e: any) => e.type === "message" && e.message?.role === "user")
		.map((e: any) => userText(e.message));
}

function leafIds(mgr: any): string[] {
	const leaves: string[] = [];
	const walk = (node: any) => {
		if (!node.children || node.children.length === 0) {
			leaves.push(node.entry.id);
			return;
		}
		for (const child of node.children) walk(child);
	};
	for (const root of mgr.getTree()) walk(root);
	return leaves;
}

function leafUserChains(mgr: any): string[][] {
	const chains = leafIds(mgr).map((id) => userChain(mgr, id));
	// Keep only chains that contain at least one user message; sort for stability.
	return chains
		.filter((c) => c.length > 0)
		.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}

const mgr = SessionManager.create(cwd, sessionDir);
// Interleave assistant replies (Pi persists the file lazily, on the first
// assistant message). User-message chains are what the comparison normalizes.
mgr.appendMessage(userMsg("ROOT"));
const a1 = mgr.appendMessage(assistantMsg("SEEN:ROOT"));
mgr.appendMessage(userMsg("MAIN"));
const a2 = mgr.appendMessage(assistantMsg("SEEN:ROOT,MAIN"));
// Branch back to the assistant after ROOT (the parent of the MAIN user turn),
// mirroring a /tree selection of the MAIN user message.
mgr.branch(a1);
mgr.appendMessage(userMsg("ALT"));
const a3 = mgr.appendMessage(assistantMsg("SEEN:ROOT,ALT"));

// Active-branch users when the ALT vs MAIN leaf is selected.
mgr.branch(a3);
const activeAlt = mgr
	.buildSessionContext()
	.messages.filter((m: any) => m.role === "user")
	.map((m: any) => userText(m));
mgr.branch(a2);
const activeMain = mgr
	.buildSessionContext()
	.messages.filter((m: any) => m.role === "user")
	.map((m: any) => userText(m));

// Name persists, applied on the ALT branch.
mgr.branch(a3);
mgr.appendSessionInfo("compare-tree");

const leafChains = leafUserChains(mgr);
const sessionFile = mgr.getSessionFile();

// Fork the active (ALT) branch into a new file.
mgr.branch(a3);
const forkMgr = SessionManager.forkFrom(sessionFile, forkCwd, sessionDir);
const forkHeader = forkMgr.getHeader();
const forkChains = leafUserChains(forkMgr);

// Durable reopen: a fresh manager rebuilds name + default leaf chain.
const reopened = SessionManager.open(sessionFile);
const reopenLeafChain = userChain(reopened, reopened.getLeafId());

const out = {
	name: reopened.getSessionName(),
	leafUserChains: leafChains,
	activeAltChain: activeAlt,
	activeMainChain: activeMain,
	forkParentRecorded: Boolean(forkHeader?.parentSession),
	forkHasAltChain: forkChains.some(
		(c: string[]) => JSON.stringify(c) === JSON.stringify(["ROOT", "ALT"]),
	),
	reopenLeafChain,
};
process.stdout.write(JSON.stringify(out) + "\n");
