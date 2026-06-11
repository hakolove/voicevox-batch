const fs = require("fs");
const path = require("path");

const inputDir = process.argv[2];
const outputFile = process.argv[3];

if (!inputDir || !outputFile) {
  console.error("Usage: node tools/repack-asar.cjs <input-dir> <output.asar>");
  process.exit(1);
}

const files = [];

function addDir(dir, node) {
  node.files = {};
  for (const name of fs.readdirSync(dir).sort()) {
    const fullPath = path.join(dir, name);
    const stat = fs.statSync(fullPath);
    if (stat.isDirectory()) {
      const child = {};
      node.files[name] = child;
      addDir(fullPath, child);
    } else if (stat.isFile()) {
      const child = { size: stat.size };
      node.files[name] = child;
      files.push({ fullPath, node: child });
    }
  }
}

const header = {};
addDir(inputDir, header);

let offset = 0;
for (const file of files) {
  file.node.offset = String(offset);
  offset += file.node.size;
}

const headerJson = Buffer.from(JSON.stringify(header), "utf8");
const padding = (4 - (headerJson.length % 4)) % 4;
const headerSize = 8 + headerJson.length + padding;
const headerBuffer = Buffer.alloc(8 + headerSize);

headerBuffer.writeUInt32LE(4, 0);
headerBuffer.writeUInt32LE(headerSize, 4);
headerBuffer.writeUInt32LE(headerSize - 4, 8);
headerBuffer.writeUInt32LE(headerJson.length, 12);
headerJson.copy(headerBuffer, 16);

const fd = fs.openSync(outputFile, "w");
try {
  fs.writeSync(fd, headerBuffer);
  for (const file of files) {
    const data = fs.readFileSync(file.fullPath);
    fs.writeSync(fd, data);
  }
} finally {
  fs.closeSync(fd);
}

console.log(`packed ${files.length} files -> ${outputFile}`);
