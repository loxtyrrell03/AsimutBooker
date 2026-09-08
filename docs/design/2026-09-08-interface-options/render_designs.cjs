// Render the original vector artwork directly, with no screenshot stitching.
const path = require('node:path');
const sharp = require('sharp');
(async () => {
  for (const number of [1, 2, 3]) {
    for (const suffix of ['', '-flow']) {
      const stem = path.join(__dirname, `option-${number}${suffix}`);
      await sharp(`${stem}.svg`, { density: 144 }).resize(1720, 1080).png().toFile(`${stem}.png`);
      console.log(`Rendered option-${number}${suffix}.png`);
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
