const fs = require('fs');
const content = fs.readFileSync('E:/智能体ai/web/src/pages/MainWorkbench.tsx', 'utf8');
const lines = content.split('\n');
console.log('Total lines:', lines.length);
console.log('Line 138 (index 137):', JSON.stringify(lines[137]));
console.log('Line 138 length:', lines[137].length);

// Check for BOM
const buffer = fs.readFileSync('E:/智能体ai/web/src/pages/MainWorkbench.tsx');
console.log('First 3 bytes (hex):', buffer.slice(0, 3).toString('hex'));

// Check around line 138
for (let i = 135; i <= 140; i++) {
  if (lines[i]) {
    console.log(`Line ${i+1} (len=${lines[i].length}):`, JSON.stringify(lines[i]));
  }
}