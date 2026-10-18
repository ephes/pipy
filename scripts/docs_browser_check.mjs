// Optional rendered-site regression check; uses an existing browser installation.
// See CONTRIBUTING.md. This command never installs Playwright or a browser.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [baseURL, outputDirectory] = process.argv.slice(2);
const modulePath = process.env.PIPY_PLAYWRIGHT_MODULE;
const executablePath = process.env.PIPY_BROWSER_EXECUTABLE;
assert(baseURL && outputDirectory && modulePath && executablePath,
  'Usage: PIPY_PLAYWRIGHT_MODULE=/path/to/index.mjs PIPY_BROWSER_EXECUTABLE=/path/to/browser node scripts/docs_browser_check.mjs URL OUTPUT_DIR');
const { chromium } = await import(pathToFileURL(resolve(modulePath)).href);
const output = resolve(outputDirectory);
await mkdir(output, { recursive: true });
const report = { pages: [], screenshots: [], errors: [] };
const browser = await chromium.launch({ executablePath, headless: true, args: ['--disable-gpu'] });
report.browser = browser.version();
const url = path => new URL(path, baseURL.endsWith('/') ? baseURL : baseURL + '/').href;
try {
  for (const [device, viewport] of [
    ['desktop', { width: 1440, height: 1000 }],
    ['mobile', { width: 390, height: 844 }],
  ]) {
    const context = await browser.newContext({ viewport, isMobile: device === 'mobile', hasTouch: device === 'mobile' });
    const page = await context.newPage();
    page.on('pageerror', error => report.errors.push(String(error)));
    const menu = () => page.locator('label.md-header__button[for="__drawer"]');
    const state = () => page.evaluate(() => {
      const sidebar = document.querySelector('.md-sidebar--primary');
      const toggle = document.querySelector('label.md-header__button[for="__drawer"]');
      const active = document.activeElement;
      const rect = active.getBoundingClientRect();
      return {
        checked: document.getElementById('__drawer').checked,
        inert: sidebar.inert,
        inside: sidebar.contains(active),
        visible: rect.right > 0 && rect.left >= 0 && rect.left < innerWidth && rect.top < innerHeight &&
          rect.bottom > 0 && getComputedStyle(active).visibility === 'visible',
        toggleFocused: active === toggle,
        focus: active.textContent.trim().slice(0, 70),
        rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom },
        expanded: toggle.getAttribute('aria-expanded'),
        outline: getComputedStyle(toggle).outlineWidth,
        pageWidth: document.documentElement.scrollWidth,
        width: innerWidth,
      };
    });
    const ready = async () => {
      await page.waitForFunction(() => document.querySelector('label[for="__drawer"][role="button"]'));
      assert.equal(await menu().getAttribute('aria-controls'), await page.locator('.md-sidebar--primary').getAttribute('id'));
    };
    const close = async () => {
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector('.md-sidebar--primary').getBoundingClientRect().right <= 0);
      const closed = await state();
      assert(closed.inert && !closed.checked && closed.toggleFocused && closed.visible);
      assert.equal(closed.expanded, 'false');
    };
    const open = async key => {
      await menu().focus();
      await page.keyboard.press(key);
      await page.waitForFunction(() => document.getElementById('__drawer').checked &&
        document.querySelector('.md-sidebar--primary').contains(document.activeElement) &&
        document.querySelector('.md-sidebar--primary').getBoundingClientRect().left >= 0);
      const opened = await state();
      assert(!opened.inert && opened.inside && opened.visible);
      assert.equal(opened.expanded, 'true');
    };
    const screenshot = async (name, target = page) => {
      await page.waitForFunction(() => document.getAnimations().every(animation =>
        animation.effect.getComputedTiming().endTime === Infinity || animation.playState !== 'running'));
      const path = resolve(output, `${device}-${name}.png`);
      await target.screenshot({ path });
      report.screenshots.push(path);
    };
    await page.goto(url('concepts/'), { waitUntil: 'networkidle' });
    await ready();
    assert.equal(await page.locator('.mermaid').count(), 2);
    await screenshot('concepts');
    await screenshot('loop', page.locator('.mermaid').first());
    await screenshot('storage', page.locator('.mermaid').nth(1));
    const initial = await state();
    assert(initial.pageWidth <= initial.width);
    const row = { device, initial, tabs: [] };
    report.pages.push(row);
    if (device === 'mobile') {
      assert(initial.inert && !initial.checked);
      await page.locator('.md-sidebar--primary a[href]').first().evaluate(link => link.focus());
      assert(!(await state()).inside, 'Closed drawer must reject programmatic focus');
      for (let i = 0; i < 8; i++) {
        await page.keyboard.press('Tab');
        const tab = await state();
        assert(!tab.inside && tab.visible, 'Closed drawer links must not enter the Tab order');
      }
      await open('Enter');
      const group = page.locator('.md-nav__item--nested > label').first();
      const before = await group.evaluate(label => document.getElementById(label.htmlFor).checked);
      await group.focus();
      await page.keyboard.press('Enter');
      assert.equal(await group.evaluate(label => document.getElementById(label.htmlFor).checked), !before);
      const visibleCount = await page.locator('.md-sidebar--primary').evaluate(sidebar =>
        [...sidebar.querySelectorAll('a[href], [tabindex="0"]')].filter(item =>
          item.getClientRects().length && getComputedStyle(item).visibility === 'visible').length);
      for (let i = 0; i < visibleCount + 3; i++) {
        await page.keyboard.press('Tab');
        const tab = await state();
        row.tabs.push(tab);
        assert(tab.inside && tab.visible, 'Open drawer Tab must stay in visible navigation: ' + JSON.stringify(tab));
      }
      await page.locator('.md-sidebar--primary a[href]').first().focus();
      await page.keyboard.press('Shift+Tab');
      assert((await state()).inside && (await state()).visible);
      await screenshot('navigation');
      await close();
      assert.notEqual((await state()).outline, '0px');
      await screenshot('focus');
      await open(' ');
      await page.locator('.md-overlay').click({ position: { x: 350, y: 300 } });
      await page.waitForFunction(() => !document.getElementById('__drawer').checked);
      assert((await state()).inert);
      await open('Enter');
      await page.keyboard.press('ControlOrMeta+k');
      const searchInput = page.getByRole('combobox');
      await searchInput.waitFor({ state: 'visible' });
      await page.waitForFunction(() => {
        let active = document.activeElement;
        while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
        return active?.getAttribute('role') === 'combobox';
      });
      assert(!(await state()).checked && (await state()).inert, 'Search must release the drawer focus cycle');
      assert(await searchInput.evaluate(input => input.getRootNode().activeElement === input));
      await searchInput.fill('provider');
      await screenshot('search');
      await page.keyboard.press('Tab');
      assert(!(await state()).inside, 'Drawer must not capture native search Tab');
      await page.keyboard.press('Escape');
      // Native search animates opacity while retaining its DOM nodes.
      await page.waitForFunction(() => {
        const input = [...document.querySelectorAll('body > div')]
          .map(host => host.shadowRoot?.querySelector('[role="combobox"]')).find(Boolean);
        return input && !input.checkVisibility({ opacityProperty: true });
      });
      await open('Enter');
      await page.setViewportSize({ width: 1440, height: 1000 });
      assert(!(await state()).inert);
      await page.setViewportSize(viewport);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector('.md-sidebar--primary').inert);
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.waitForFunction(() => !document.querySelector('.md-sidebar--primary').inert);
      await page.setViewportSize(viewport);
      await page.waitForFunction(() => document.querySelector('.md-sidebar--primary').inert);
      await open('Enter');
      const destination = page.locator('.md-sidebar--primary a[href$="quickstart/"]');
      const parentGroup = destination.locator('xpath=ancestor::li[contains(@class,"md-nav__item--nested")][1]');
      if (!(await parentGroup.locator('input').first().isChecked())) {
        await parentGroup.locator('label').first().click();
      }
      await destination.click();
      await page.waitForURL(url('quickstart/'));
      await ready();
      await open(' ');
      await close();
      await page.goBack({ waitUntil: 'networkidle' });
      await ready();
      // Native browser history may restore the checkbox; start from a closed drawer.
      if ((await state()).checked) await close();
      await open('Enter');
      await close();
    } else {
      assert(!initial.inert);
      await page.keyboard.press('Tab');
      assert((await state()).visible);
    }
    await page.goto(url('quickstart/'), { waitUntil: 'networkidle' });
    await ready();
    row.code = await page.locator('pre').evaluateAll(blocks => blocks.map(block => ({
      width: block.clientWidth, scrollWidth: block.scrollWidth,
      overflow: getComputedStyle(block).overflowX,
      right: block.getBoundingClientRect().right,
    })));
    assert((await state()).pageWidth <= (await state()).width);
    assert(row.code.every(block => block.right <= viewport.width + 1));
    await page.locator('#first-session').evaluate(heading =>
      window.scrollTo({ top: heading.getBoundingClientRect().top + scrollY - 80, behavior: 'instant' }));
    await screenshot('quickstart');
    await page.locator('#authenticate-or-configure-a-model').evaluate(heading =>
      window.scrollTo({ top: heading.getBoundingClientRect().top + scrollY - 80, behavior: 'instant' }));
    await screenshot('provider-setup');
    await context.close();
  }
  assert.deepEqual(report.errors, []);
  console.log('Rendered desktop/mobile navigation, focus, search, history and overflow checks passed.');
} finally {
  await writeFile(resolve(output, 'browser-report.json'), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
}
