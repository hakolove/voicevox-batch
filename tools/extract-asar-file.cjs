const fs = require("fs");
const path = require("path");

const asarFile = process.argv[2];
const asarPath = process.argv[3];
const outputFile = process.argv[4];

if (!asarFile || !asarPath || !outputFile) {
  console.error("Usage: node tools/extract-asar-file.cjs <app.asar> <path/in/asar> <output-file>");
  process.exit(1);
}

function readHeader(file) {
  const fd = fs.openSync(file, "r");
  const header = Buffer.alloc(16);
  fs.readSync(fd, header, 0, 16, 0);
  const headerSize = header.readUInt32LE(4);
  const jsonSize = header.readUInt32LE(12);
  const json = Buffer.alloc(jsonSize);
  fs.readSync(fd, json, 0, jsonSize, 16);
  return {
    fd,
    base: 8 + headerSize,
    tree: JSON.parse(json.toString("utf8").replace(/\0+$/, "")),
  };
}

const { fd, base, tree } = readHeader(asarFile);
try {
  let node = tree;
  for (const part of asarPath.split("/")) {
    node = node.files && node.files[part];
    if (!node) throw new Error(`Not found in asar: ${asarPath}`);
  }
  if (node.files) throw new Error(`Path is a directory: ${asarPath}`);
  const buffer = Buffer.alloc(node.size);
  fs.readSync(fd, buffer, 0, node.size, base + Number(node.offset || 0));
  fs.mkdirSync(path.dirname(outputFile), { recursive: true });
  fs.writeFileSync(outputFile, buffer);
  console.log(`extracted ${asarPath} -> ${outputFile}`);
} finally {
  fs.closeSync(fd);
}
