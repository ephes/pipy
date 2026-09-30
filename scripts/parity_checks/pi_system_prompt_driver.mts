/**
 * Build system prompts with the local Pi reference's REAL
 * `buildSystemPromptSections` and print them as one JSON document, so pipy's
 * section builder can be pinned against Pi's own output
 * (`tests/fixtures/pi_system_prompt_sections.json`,
 * `tests/test_native_system_prompt_sections.py`).
 *
 * Each case names its inputs; the tool snippets and guidelines are Pi's
 * built-in `*ToolSystemPromptContribution` constants (the bash guideline is
 * included only where a case asks for it: pipy does not expose Pi's session
 * environment variables, so its bash tool carries no guideline).
 *
 * Usage (run from source the way pi-mono runs its TypeScript):
 *   PI_MONO_DIR=/path/to/pi-mono \
 *     node --import "$PI_MONO_DIR/packages/coding-agent/src/experimental/source-resolver.ts" pi_system_prompt_driver.mts
 */

const piMono = process.env.PI_MONO_DIR;
if (!piMono) {
	process.stderr.write("PI_MONO_DIR is not set\n");
	process.exit(2);
}
const base = `file://${piMono.endsWith("/") ? piMono : piMono + "/"}`;
const src = (path: string) => new URL(`packages/coding-agent/src/${path}`, base).href;

const { buildSystemPromptSections } = await import(src("core/system-prompt.ts"));
const { getReadmePath, getDocsPath, getExamplesPath } = await import(src("config.ts"));
const contributions: Record<string, { snippet: string; guidelines: readonly string[] }> = {
	read: (await import(src("core/tools/read.ts"))).readToolSystemPromptContribution,
	bash: (await import(src("core/tools/bash.ts"))).bashToolSystemPromptContribution,
	edit: (await import(src("core/tools/edit.ts"))).editToolSystemPromptContribution,
	write: (await import(src("core/tools/write.ts"))).writeToolSystemPromptContribution,
	grep: (await import(src("core/tools/grep.ts"))).grepToolSystemPromptContribution,
	find: (await import(src("core/tools/find.ts"))).findToolSystemPromptContribution,
	ls: (await import(src("core/tools/ls.ts"))).lsToolSystemPromptContribution,
};

function toolInputs(bashGuideline: boolean) {
	const toolSnippets: Record<string, string> = {};
	const toolGuidelines: Record<string, string[]> = {};
	for (const [name, contribution] of Object.entries(contributions)) {
		toolSnippets[name] = contribution.snippet;
		if (name === "bash" && !bashGuideline) continue;
		if (contribution.guidelines.length > 0) toolGuidelines[name] = [...contribution.guidelines];
	}
	return { toolSnippets, toolGuidelines };
}

function skill(name: string, description: string, filePath: string, disableModelInvocation = false) {
	return {
		name,
		description,
		filePath,
		baseDir: filePath.slice(0, filePath.lastIndexOf("/")),
		sourceInfo: { path: filePath, source: "local" },
		disableModelInvocation,
	};
}

const skills = [
	skill("lint", "Lint & fix <code> \"quoted\" 'single'", "/skills/lint/SKILL.md"),
	skill("hidden", "Only by command", "/skills/hidden/SKILL.md", true),
	skill("deploy", "Deploy the app", "/skills/deploy.md"),
];

const cases: Record<string, object> = {
	pi_default: { selectedTools: ["read", "bash", "edit", "write"], ...toolInputs(true) },
	pipy_default: {
		selectedTools: ["read", "ls", "grep", "find", "write", "edit", "bash"],
		...toolInputs(false),
	},
	bash_only_skills: { selectedTools: ["bash"], ...toolInputs(false), skills },
	read_skills_context: {
		selectedTools: ["read", "edit"],
		...toolInputs(false),
		skills,
		appendSystemPrompt: "APPENDED",
		contextFiles: [
			{ path: "/w/AGENTS.md", content: "Root rules" },
			{ path: "/w/sub/AGENTS.md", content: "Sub rules" },
		],
	},
	no_tools: { selectedTools: [], ...toolInputs(false), skills },
	custom_prompt: {
		customPrompt: "CUSTOM",
		selectedTools: ["read", "bash"],
		...toolInputs(false),
		skills,
		appendSystemPrompt: "APPENDED",
	},
	unknown_tool_only: { selectedTools: ["mytool"], ...toolInputs(false) },
	all_hidden_skills: { selectedTools: ["read"], ...toolInputs(false), skills: [skills[1]] },
};

const out: Record<string, unknown> = {
	paths: { readme: getReadmePath(), docs: getDocsPath(), examples: getExamplesPath() },
	cases: {},
};
for (const [name, input] of Object.entries(cases)) {
	(out.cases as Record<string, unknown>)[name] = {
		input,
		sections: Object.entries(buildSystemPromptSections({ cwd: "C:\\w\\proj", ...input })),
	};
}
process.stdout.write(`${JSON.stringify(out, null, 2)}\n`);
