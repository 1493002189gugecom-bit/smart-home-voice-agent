async page => {
  await page.goto('http://127.0.0.1:8785/tests/scene-check.html');
  const result = await page.evaluate(async () => {
    // Import the same Three.js instance used by the real loader (Vite adds a cache key).
    const source = await (await fetch('/src/home-furniture.ts')).text();
    const THREE = await import(source.match(/from "([^"]+three\.js[^\"]*)"/)[1]);
    const { furnitureLoader, furniture } = await import('/src/home-furniture.ts');
    const groups = {}, loader = furnitureLoader(() => {});
    const checks = [];
    const check = (name, pass, evidence) => checks.push({ name, pass, evidence });
    const dimensions = { living_room: [5.9, 4.3], bedroom: [3.55, 4.55], kitchen: [3.22, 2.62] };
    try {
      for (const room of Object.keys(furniture)) { groups[room] = new THREE.Group(); loader.add(groups[room], room); }
      const deadline = performance.now() + 5000;
      while (!Object.values(groups).flatMap(g => g.children).every(h => h.userData.asset)) {
        if (performance.now() > deadline) throw Error('Furniture failed to load');
        await new Promise(resolve => setTimeout(resolve, 20));
      }
      Object.values(groups).forEach(g => g.updateMatrixWorld(true));
      const bounds = h => new THREE.Box3().setFromObject(h);
      const find = (room, asset) => groups[room].children.find(h => h.userData.asset === asset);
      // These assets' seat/door/control faces point toward native +Z, independently
      // checked against model previews. A positive dot faces the room/table.
      const front = h => new THREE.Vector3(0, 0, 1).applyQuaternion(h.quaternion);
      const inward = h => front(h).dot(new THREE.Vector3(-h.position.x, 0, -h.position.z));
      const bed = find('bedroom', 'bed_double_A'), bedBox = bounds(bed);
      const high = [];
      bed.traverse(o => {
        if (!o.isMesh) return;
        const p = o.geometry.attributes.position;
        for (let i = 0; i < p.count; i++) {
          const v = new THREE.Vector3().fromBufferAttribute(p, i).applyMatrix4(o.matrixWorld);
          if (v.y > bedBox.max.y * 0.9) high.push(v);
        }
      });
      const headZ = high.reduce((sum, p) => sum + p.z, 0) / high.length;
      check('bed headboard backs onto bedroom wall', headZ > bed.position.z && 2.245 - bedBox.max.z >= 0 && 2.245 - bedBox.max.z < 0.15,
        { headZ, wallGap: 2.245 - bedBox.max.z });
      const kitchen = groups.kitchen.children.filter(h => h.userData.asset.startsWith('kenney/'));
      check('kitchen doors and stove controls face the working aisle', kitchen.every(h => front(h).z < -0.9), kitchen.map(h => ({asset:h.userData.asset,front:front(h).toArray()})));
      check('kitchen units have backs aligned near the wall', kitchen.every(h => 1.28 - bounds(h).max.z >= 0 && 1.28 - bounds(h).max.z < 0.09)
        && Math.max(...kitchen.map(h => bounds(h).max.z)) - Math.min(...kitchen.map(h => bounds(h).max.z)) < 0.015,
        kitchen.map(h => ({asset:h.userData.asset,back:bounds(h).max.z})));
      const sofa = find('living_room', 'couch_pillows');
      check('sofa has its back near the wall and faces inward', 2.12 - bounds(sofa).max.z >= 0 && 2.12 - bounds(sofa).max.z < 0.15 && front(sofa).z < -0.9,
        {wallGap:2.12 - bounds(sofa).max.z});
      const cabinets = ['living_room', 'bedroom'].map(room => find(room, room === 'living_room' ? 'cabinet_medium_decorated' : 'cabinet_medium'));
      check('storage backs onto a wall with drawers facing inward', cabinets.every((h,i) => inward(h) > 0 && bounds(h).min.x + dimensions[i===0?'living_room':'bedroom'][0]/2 - 0.03 >= 0
        && bounds(h).min.x + dimensions[i===0?'living_room':'bedroom'][0]/2 - 0.03 < 0.15), cabinets.map(h => ({asset:h.userData.asset,inward:inward(h)})));
      const table = find('kitchen', 'table_medium');
      const chairs = groups.kitchen.children.filter(h => h.userData.asset === 'chair_A');
      check('both dining chairs face the table', chairs.every(h => front(h).dot(table.position.clone().sub(h.position)) > 0.5), chairs.map(h => front(h).toArray()));
      const lampCases = [
        [find('bedroom', 'lamp_table'), find('bedroom', 'cabinet_small')],
        [find('kitchen', 'lamp_table'), table]
      ];
      check('table lamps rest on furniture tops', lampCases.every(([lamp,support]) => Math.abs(bounds(lamp).min.y - bounds(support).max.y) < 0.015),
        lampCases.map(([lamp,support]) => bounds(lamp).min.y - bounds(support).max.y));
      const picture = find('bedroom', 'pictureframe_large_A'), pictureBox = bounds(picture);
      check('picture faces inward and fits below wall top', front(picture).z < -0.9 && pictureBox.max.y < 1.52 && pictureBox.max.z < 2.245 && 2.245-pictureBox.max.z < 0.08,
        {max:pictureBox.max.toArray(),front:front(picture).toArray()});
      const outside = [], collisions = [];
      for (const [room,g] of Object.entries(groups)) {
        const [width,depth] = dimensions[room];
        for (const h of g.children) {
          const b = bounds(h);
          if (b.min.x < -width/2+0.03 || b.max.x > width/2-0.03 || b.min.z < -depth/2+0.03 || b.max.z > depth/2-0.03)
            outside.push({room,asset:h.userData.asset});
        }
        const solid = g.children.filter(h => !h.userData.asset.startsWith('rug_'));
        for (let i=0;i<solid.length;i++) for(let j=i+1;j<solid.length;j++) {
          const overlap = bounds(solid[i]).intersect(bounds(solid[j])).getSize(new THREE.Vector3());
          if (overlap.x>0.005 && overlap.y>0.005 && overlap.z>0.005) collisions.push({room,a:solid[i].userData.asset,b:solid[j].userData.asset});
        }
      }
      check('furniture stays inside room floor and walls', outside.length === 0, outside);
      check('solid furniture does not interpenetrate', collisions.length === 0, collisions);
      const aisle = Math.min(...kitchen.map(h => bounds(h).min.z)) - bounds(table).max.z;
      check('kitchen preserves a clear working aisle', aisle >= 0.55, {aisle});
      return { checks, modelCount:Object.values(groups).reduce((n,g)=>n+g.children.length,0) };
    } finally { loader.dispose(); }
  });
  if (result.checks.some(c => !c.pass)) throw Error(JSON.stringify(result));
  await page.setViewportSize({width:1280,height:800});
  await page.waitForFunction(()=>scene.inspect().models.length===21);
  await page.screenshot({path:'output/playwright/layout-fixed-wide.png'});
  return result;
}
