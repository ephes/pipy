// Render Pi's ThinkingSelectorComponent and ModelSelectorComponent with stubs
// (pi-mono 1b347794e). Regenerate renders.json from ~/src/pi-mono with:
//   node --import ./packages/coding-agent/src/experimental/source-resolver.ts \
//     <pipy>/tests/fixtures/pi_selectors/render.mts > <pipy>/tests/fixtures/pi_selectors/renders.json
// The stub runtime's catalog refresh never settles, so Pi shows its
// "Refreshing model catalogs…" line; pipy has no refresh and the test drops it.
const root = '/Users/jochen/src/pi-mono/packages';
const theme = await import(`${root}/coding-agent/src/modes/interactive/theme/theme.ts`);
const tui = await import(`${root}/tui/src/index.ts`);
const kb = await import(`${root}/coding-agent/src/core/keybindings.ts`);
const { ThinkingSelectorComponent } = await import(`${root}/coding-agent/src/modes/interactive/components/thinking-selector.ts`);
const { ModelSelectorComponent } = await import(`${root}/coding-agent/src/modes/interactive/components/model-selector.ts`);

theme.initTheme('dark');
tui.setKeybindings(new kb.KeybindingsManager());
const strip = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, '').replace(/\x1b_[^\x07]*\x07/g, '');
const KEYS: Record<string, string> = { up: '\x1b[A', down: '\x1b[B', enter: '\r', esc: '\x1b', tab: '\t', 'ctrl-s': '\x13', backspace: '\x7f' };
const feed = (c: { handleInput(d: string): void }, keys: string[]) => {
	for (const k of keys) c.handleInput(KEYS[k] ?? k);
};
const out: Record<string, unknown> = {};

// Thinking selector.
{
	const t = new ThinkingSelectorComponent('high', ['off', 'minimal', 'low', 'medium', 'high', 'xhigh'], () => {}, () => {}, () => {}, 'medium');
	out.thinking_initial = t.render(80).map(strip);
	feed(t, ['h', 'i']);
	out.thinking_hi = t.render(80).map(strip);
	feed(t, ['backspace', 'backspace', 'z', 'z', 'z']);
	out.thinking_none = t.render(80).map(strip);
}

// Model selector.
const mk = (provider: string, id: string, name: string) => ({ provider, id, name });
const models = [
	mk('openai', 'gpt-5.5', 'GPT-5.5'),
	mk('anthropic', 'claude-sonnet-4-5', 'Claude Sonnet 4.5'),
	mk('openai-codex', 'gpt-6.1-sol', 'GPT-6.1 Sol'),
	mk('anthropic', 'claude-opus-4-5', 'Claude Opus 4.5'),
	mk('openrouter', 'openai/gpt-5.5', 'OpenAI: GPT-5.5'),
	mk('openai', 'gpt-4o', 'GPT-4o'),
];
const runtime = {
	getAvailableSnapshot: () => models,
	getModel: (p: string, i: string) => models.find((m) => m.provider === p && m.id === i),
	getError: () => undefined,
	refresh: () => new Promise(() => {}),
};
const fakeTui = { requestRender() {} };
const render = (scoped: typeof models, search: string | undefined, keys: string[]) => {
	const s = new ModelSelectorComponent(fakeTui as never, models[2] as never, runtime as never, scoped.map((m) => ({ model: m })) as never, () => {}, () => {}, search, () => {}, { provider: 'anthropic', id: 'claude-opus-4-5' });
	feed(s, keys);
	const lines = s.render(80).map(strip);
	s.dispose();
	return lines;
};
out.model_initial = render([], undefined, []);
out.model_search_gpt = render([], 'gpt', []);
out.model_search_openai_gpt = render([], 'openai/gpt', []);
out.model_search_def = render([], 'def', []);
out.model_typed_down = render([], undefined, ['o', 'p', 'u', 's', 'down']);
out.model_none = render([], 'zzzz', []);
out.model_scoped = render([models[5], models[0]], undefined, []);
out.model_scoped_tab = render([models[5], models[0]], undefined, ['tab']);
console.log(JSON.stringify(out, null, 1));
process.exit(0);
