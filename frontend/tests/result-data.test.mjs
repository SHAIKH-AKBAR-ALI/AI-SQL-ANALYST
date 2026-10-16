import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Load the actual pure TS module without adding a frontend test framework.
const source = readFileSync(new URL('../app/result-data.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { csvCell, resultCSV, chartData } = await import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
const result = { columns: ['product_name', 'revenue'], rows: [['Milk', '1050.00'], ['Potato', '300.00']], truncated: false };

test('CSV protects formula prefixes, controls, headers and quote escaping', () => {
  for (const value of ['=1+1', '+SUM(A1)', '-1', '@SUM(A1)', ' \t=HYPERLINK("evil")', '\r\n+1']) assert.ok(csvCell(value).startsWith('"\''));
  assert.equal(csvCell('Milk,"large"'), '"Milk,""large"""');
  assert.equal(csvCell(null), '""');
  assert.ok(resultCSV({ columns: ['=bad'], rows: [['safe']] }).includes('"\'=bad"'));
});

test('chart accepts common-scale result and omits misleading shapes', () => {
  assert.deepEqual(chartData(result), [{ label: 'Milk', value: 1050 }, { label: 'Potato', value: 300 }]);
  for (const invalid of [{ ...result, truncated: true }, { ...result, columns: ['unit', 'quantity'] },
                         { ...result, rows: [['Milk', null], ['Potato', '3']] },
                         { ...result, rows: [['Milk', 'NaN'], ['Potato', '3']] },
                         { ...result, rows: [['Milk', '1'], ['Milk', '3']] }]) assert.equal(chartData(invalid), null);
});
