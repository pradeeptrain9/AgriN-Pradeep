import { validateNodeUrl, nodeLabel, parseNodeUrl } from './src/services/node';
let pass = 0, fail = 0;
const check = (n: string, c: boolean, e = '') => {
  if (c) { pass++; console.log(`  ok   ${n}`); } else { fail++; console.log(`  FAIL ${n} ${e}`); }
};

console.log('\n== label (was showing the whole URL) ==');
check('https host only', nodeLabel('https://node-in.agrin.example') === 'node-in.agrin.example',
  `got ${nodeLabel('https://node-in.agrin.example')}`);
check('keeps port', nodeLabel('http://10.0.2.2:8099') === '10.0.2.2:8099',
  `got ${nodeLabel('http://10.0.2.2:8099')}`);
check('strips path', nodeLabel('https://a.example/x/y') === 'a.example');

console.log('\n== https-only guard (never ran before) ==');
check('https accepted', validateNodeUrl('https://node-br.agrin.example').valid);
const cleartext = validateNodeUrl('http://node-br.agrin.example');
check('http to real host REJECTED', !cleartext.valid, JSON.stringify(cleartext));
check('rejection explains why', (cleartext.problem ?? '').includes('unprotected'));
check('http to 10.0.2.2 allowed (emulator host)', validateNodeUrl('http://10.0.2.2:8099').valid);
check('http to localhost allowed', validateNodeUrl('http://localhost:8099').valid);
check('http to 127.0.0.1 allowed', validateNodeUrl('http://127.0.0.1:8099').valid);

console.log('\n== hostile / malformed input ==');
for (const bad of ['', '   ', 'node-in.agrin.example', 'ftp://a.example',
                   'javascript:alert(1)', 'file:///etc/passwd', 'https://', 'not a url']) {
  const r = validateNodeUrl(bad);
  check(`rejects ${JSON.stringify(bad)}`, !r.valid, JSON.stringify(r));
}
check('no scheme is rejected, not silently prefixed',
  !validateNodeUrl('node-in.agrin.example').valid);

console.log('\n== normalisation ==');
check('trailing slash removed',
  validateNodeUrl('https://a.example/').normalised === 'https://a.example');
check('whitespace trimmed',
  validateNodeUrl('  https://a.example  ').normalised === 'https://a.example');
check('host lowercased in label', nodeLabel('https://NODE-IN.Agrin.Example') === 'node-in.agrin.example');

console.log('\n== nothing throws (the actual RN bug) ==');
let threw = false;
for (const v of ['https://a.example', 'http://x', '', 'garbage', '://', 'https://a:99999/p?q#f']) {
  try { validateNodeUrl(v); nodeLabel(v); parseNodeUrl(v); } catch { threw = true; }
}
check('no getter throws on any input', !threw);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
