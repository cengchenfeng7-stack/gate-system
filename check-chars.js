const fs = require('fs');
const buffer = fs.readFileSync('E:/智能体ai/web/src/pages/MainWorkbench.tsx');
const lines = fs.readFileSync('E:/智能体ai/web/src/pages/MainWorkbench.tsx', 'utf8').split('\n');

// Check raw bytes around line 138
const line138 = lines[137];
console.log('Line 138 bytes:');
for (let i = 0; i < line138.length; i++) {
  const char = line138[i];
  const code = line138.charCodeAt(i);
  if (code > 127 || code < 32) {
    console.log(`Index ${i}: char=${JSON.stringify(line138[i])} code=${code} hex=${code.toString(16)}`);
  }
}

// Check for any non-ASCII characters in the whole file
console.log('\nNon-ASCII characters in file:');
const content = fs.readFileSync('E:/智能体ai/web/src/pages/MainWorkbench.tsx', 'utf8');
for (let i = 0; i < content.length; i++) {
  const code = content.charCodeAt(i);
  if (code > 127) {
    console.log(`Position ${i}: char=${JSON.stringify(content[i])} code=${content.charCodeAt(i)} hex=${content.charCodeAt(i).toString(16)} context=${JSON.stringify(content.slice(Math.max(0,i-10), i+10))}`);
  }
}