async page => {
  await page.goto('http://127.0.0.1:8785/tests/scene-check.html');
  await page.waitForFunction(() => scene.inspect().models.length === 21);
  await page.evaluate(() => clearInterval(keep));
  const checks = [];
  const check = (condition, name) => { if (!condition) throw Error(name); checks.push(name); };
  const update = async devices => page.evaluate(devices => {
    sample.devices = devices; refresh();
  }, devices);
  const device = id => ({id,room_id:'living_room',type:'switch',online:true,state:{on:true}});
  await update([device('a'), device('b')]);
  const before = await page.evaluate(() => scene.inspect().devices.find(d => d.id === 'b').position);
  await update([device('b'), device('c')]);
  let values = await page.evaluate(() => scene.inspect().devices);
  const b = values.find(d => d.id === 'b'), c = values.find(d => d.id === 'c');
  check(JSON.stringify(b.position) === JSON.stringify(before), 'existing device keeps its position after inventory change');
  check(JSON.stringify(b.position) !== JSON.stringify(c.position), 'new device receives a unique position');
  check(!values.find(d => d.id === 'a').available, 'missing device becomes unavailable');
  await update([{...device('b'), room_id:'bedroom'}]);
  values = await page.evaluate(() => scene.inspect().devices);
  check(values.find(d => d.id === 'b').room === 'bedroom', 'room change rebuilds bound node');
  await update([{...device('b'), room_id:'bedroom', type:'light',state:{on:true,brightness:50}}]);
  await page.waitForTimeout(650);
  values = await page.evaluate(() => scene.inspect().devices);
  check(values.find(d => d.id === 'b').type === 'light' && values.find(d => d.id === 'b').lightIntensity > 2.9, 'type change rebuilds correct effect');
  await update([{id:'sensor',room_id:'living_room',type:'sensor',online:true,state:{temperature:26}}]);
  for (const [backend, suffix] of [['ha','实测'],['memory','模拟'],[null,'读数']]) {
    const text = await page.evaluate(backend => {
      if(backend) sample.backend=backend; else delete sample.backend;
      refresh(); return scene.inspect().devices.find(d=>d.id==='sensor').text;
    }, backend);
    check(text === `26°C ${suffix}`, `temperature provenance: ${backend ?? 'unspecified'}`);
  }
  await page.evaluate(() => scene.dispose());
  return {checks,final:await page.evaluate(()=>scene.inspect())};
}
