const fs = require('fs');
const lines = fs.readFileSync('E:/智能体ai/web/src/pages/MainWorkbench.tsx', 'utf8').split('\n');
console.log('Line 138 length:', lines[137].length);
console.log('Line 138:', JSON.stringify(lines[137]));
console.log('Char at 136:', JSON.stringify(lines[137].charAt(136)));
console.log('Char at 135:', JSON.stringify(lines[137].charAt(135)));