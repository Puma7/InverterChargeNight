// node render.js <svg> <png> <width> <height> [background]; COLOR_SCHEME=dark for the dark variant
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [svg, png, w, h, bg] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: +w, height: +h }, colorScheme: process.env.COLOR_SCHEME || 'light', deviceScaleFactor: +(process.env.SCALE || 1) });
  const data = 'data:image/svg+xml;base64,' + fs.readFileSync(svg).toString('base64');
  await page.setContent(`<html><body style="margin:0;background:${bg || 'transparent'}"><img src="${data}" style="display:block;width:${w}px;height:${h}px"></body></html>`);
  await page.waitForTimeout(100);
  await page.screenshot({ path: png, omitBackground: !bg });
  await browser.close();
})();
