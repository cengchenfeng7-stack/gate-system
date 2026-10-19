const fs = require('fs');
const lines = fs.readFileSync('E:/智能体ai/web/src/pages/MainWorkbench.tsx', 'utf8').split('\n');
for (let i = 130; i <= 140; i++) {
  console.log(i + 1, '[' + lines[i].length + ']:', JSON.stringify(lines[i]));
}