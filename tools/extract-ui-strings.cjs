const fs = require("fs");

const file = process.argv[2];
const maxLength = Number(process.argv[3] || 120);
if (!file) {
  console.error("Usage: node tools/extract-ui-strings.cjs <file>");
  process.exit(1);
}

const source = fs.readFileSync(file, "utf8");
const japanese = /[ぁ-んァ-ン一-龯々ー]/;
const quoteRe = /(["'`])((?:\\.|(?!\1)[\s\S])*?)\1/g;
const values = new Map();
let match;

while ((match = quoteRe.exec(source))) {
  const value = match[2]
    .replace(/\\n/g, "\n")
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'")
    .replace(/\\`/g, "`");

  if (value.length <= maxLength && japanese.test(value)) {
    values.set(value, (values.get(value) || 0) + 1);
  }
}

for (const [value, count] of [...values.entries()].sort((a, b) => b[1] - a[1])) {
  console.log(`${count}\t${value.replace(/\n/g, "\\n")}`);
}
