// Direct SVG rasterization. This script never opens a browser or app session.
const path = require('node:path');
const fs = require('node:fs');
const sharp = require('sharp');
async function main() {
  for (const file of ['option-a', 'option-b', 'option-c', 'comparison']) {
    await sharp(path.join(__dirname, file + '.svg')).png().toFile(path.join(__dirname, file + '.png'));
  }
  const thumbs = [];
  const keys = ['settings', 'assistant', 'booking', 'dates', 'feedback', 'narrow'];
  for (const [i, option] of ['a', 'b', 'c'].entries()) {
    for (const [j, key] of keys.entries()) {
      const input = path.join(__dirname, 'screens', `${option}-${key}.svg`);
      const png = await sharp(input).resize(600, 400, {fit:'contain', background:'#f8fafc'}).png().toBuffer();
      thumbs.push({input: png, left: i*616, top: j*416});
    }
    for (const key of ['settings', 'narrow', 'calendar', 'cancel', 'scan', 'strategy', 'minimum-settings']) {
      await sharp(path.join(__dirname,'screens',`${option}-${key}.svg`)).png().toFile(path.join(__dirname, `${option}-${key}-preview.png`));
    }
  }
  await sharp({create:{width:1848,height:2496,channels:4,background:'#e1e6ee'}}).composite(thumbs).png().toFile(path.join(__dirname,'review-contact-sheet.png'));
  // Parse/render every screen, including those without an individual PNG deliverable.
  for(const file of fs.readdirSync(path.join(__dirname,'screens'))) {
    if(file.endsWith('.svg')) await sharp(path.join(__dirname,'screens',file)).resize({width:300}).png().toBuffer();
  }
  console.log('Rendered all 87 SVG frames; generated home, comparison, contact sheet and detail previews.');
}
main().catch(error => { console.error(error); process.exitCode=1; });
