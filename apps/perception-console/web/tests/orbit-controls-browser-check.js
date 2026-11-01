// 正式网页课程验收：真实 glTF 2.0 + 外部资源，以及 OrbitControls 的常用鼠标操作。
// 从仓库根目录交给 Playwright CLI run-code --filename 执行；仅截图，不录制视频。
// 此测试捕捉 glTF 入口退回 GLB、资源缺失、旋转/缩放/平移失效与重置失效。
async page => {
  const url = 'http://127.0.0.1:8770/walkthrough?model=gltf';
  const modelUrl = 'http://127.0.0.1:8770/assets/models/smart-home-gltf/smart-home.gltf';
  const modelDirectory = modelUrl.slice(0, modelUrl.lastIndexOf('/') + 1);
  const checks = [], errors = [], responses = new Map();
  const assert = (ok, message) => { if (!ok) throw Error(message); };
  const consoleError = message => {
    if (message.type() === 'error' || /Couldn.t load texture|Content Security Policy|WebGL.*(error|warning)/i.test(message.text())) errors.push(message.text());
  };
  const pageError = error => errors.push(error.message);
  const networkResponse = response => responses.set(response.url(), response);
  const failedRequest = request => {
    if (request.url().startsWith(modelDirectory)) errors.push('资源请求失败：' + request.url() + ' ' + (request.failure()?.errorText ?? ''));
  };
  let canvas;
  page.on('console', consoleError);
  page.on('pageerror', pageError);
  page.on('response', networkResponse);
  page.on('requestfailed', failedRequest);
  try {
    await page.setViewportSize({ width: 1280, height: 800 });
    // 在导航前监听，不能用测试自行 fetch 的成功掩盖页面仍加载 GLB。
    // 先把拒绝转换成结果，避免导航异常时留下未处理的 Promise 拒绝。
    const modelRequest = page.waitForResponse(response => response.url() === modelUrl, { timeout: 10000 })
      .then(response => ({ response }), () => ({ error: '未请求 glTF 模型（旧入口仍使用 GLB）：' + modelUrl }));
    const navigation = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    assert(navigation?.status() === 200, 'glTF 漫游页面无法访问');
    const modelResult = await modelRequest;
    assert(!modelResult.error, modelResult.error);
    const modelResponse = modelResult.response;
    assert(modelResponse.status() === 200, 'glTF 模型响应不是 200：' + modelResponse.status());
    const modelBytes = await modelResponse.body();
    let gltf;
    try { gltf = JSON.parse(modelBytes.toString('utf8')); }
    catch { throw Error('模型响应不是 glTF JSON，不能用 GLB 替代 .gltf'); }
    assert(gltf.asset?.version === '2.0', '模型不是 glTF 2.0 JSON');
    assert(gltf.scenes?.length > 0 && gltf.meshes?.length > 0, 'glTF 未包含可显示的场景和网格');
    checks.push({ item: 'load-gltf', url: modelResponse.url(), status: modelResponse.status(), version: gltf.asset.version, bytes: modelBytes.length });

    // 核对 JSON 声明的每个 URI；响应必须来自页面加载，测试不补发资源请求。
    const references = [
      ...(gltf.buffers ?? []).map((value, index) => ({ kind: 'buffer', index, uri: value.uri, byteLength: value.byteLength })),
      ...(gltf.images ?? []).map((value, index) => ({ kind: 'image', index, uri: value.uri })),
    ];
    const external = references.filter(reference => typeof reference.uri === 'string' && !/^(data|blob):/i.test(reference.uri));
    assert(external.some(reference => reference.kind === 'buffer'), 'glTF 未引用外部二进制 buffer');
    assert(external.some(reference => reference.kind === 'image'), 'glTF 未引用外部图片');
    const resources = [];
    for (const reference of external) {
      assert(reference.uri.trim().length > 0, 'glTF 存在空资源 URI');
      const resourceUrl = await page.evaluate(({ uri, base }) => new URL(uri, base).href, { uri: reference.uri, base: modelResponse.url() });
      let resourceResponse = responses.get(resourceUrl);
      if (!resourceResponse) {
        try { resourceResponse = await page.waitForResponse(response => response.url() === resourceUrl, { timeout: 10000 }); }
        catch { throw Error('页面未成功请求 glTF 外部资源：' + resourceUrl); }
      }
      assert(resourceResponse.status() === 200, 'glTF 外部资源响应不是 200：' + resourceUrl + ' (' + resourceResponse.status() + ')');
      const resourceFailure = await resourceResponse.finished();
      assert(!resourceFailure, 'glTF 外部资源未完整加载：' + resourceUrl + ' ' + (resourceFailure?.message ?? ''));
      const bytes = await resourceResponse.body();
      let resourceBytes = bytes.length, byteEvidence = 'response-body';
      if (reference.kind === 'image') {
        assert(/^image\//i.test(resourceResponse.headers()['content-type'] ?? ''), 'glTF 图片响应类型不是 image/*：' + resourceUrl);
        // CLI 对图片响应体偶尔返回空 Buffer；仅使用当前页面真实请求的传输完成证据。
        if (resourceBytes === 0) {
          const timing = await page.waitForFunction(resourceUrl => {
            const entries = performance.getEntriesByName(resourceUrl, 'resource');
            const entry = entries[entries.length - 1];
            return entry?.decodedBodySize > 0 ? entry.decodedBodySize : false;
          }, resourceUrl, { timeout: 10000 });
          resourceBytes = await timing.jsonValue();
          await timing.dispose();
          byteEvidence = 'page-resource-timing';
        }
      }
      assert(resourceBytes > 0, 'glTF 外部资源为空：' + resourceUrl);
      assert(!/^\s*(?:<!doctype\s+html|<html)/i.test(bytes.subarray(0, 100).toString('utf8')), 'glTF 资源被 HTML 入口替代：' + resourceUrl);
      if (reference.kind === 'buffer') assert(Number.isInteger(reference.byteLength) && bytes.length >= reference.byteLength, 'glTF 外部 buffer 字节数不完整：' + resourceUrl);
      resources.push({ kind: reference.kind, index: reference.index, uri: reference.uri, url: resourceUrl, status: resourceResponse.status(), bytes: resourceBytes, byteEvidence });
    }
    checks.push({ item: 'external-gltf-resources', buffers: resources.filter(resource => resource.kind === 'buffer').length, images: resources.filter(resource => resource.kind === 'image').length, resources });

    canvas = page.locator('.walkthrough-canvas');
    await canvas.waitFor({ state: 'visible', timeout: 10000 });
    try { await page.locator('.walkthrough-loading').waitFor({ state: 'hidden', timeout: 15000 }); }
    catch { throw Error('glTF 资源已请求，但场景未完成显示：' + await page.locator('.walkthrough-loading').innerText().catch(() => '加载状态未结束')); }
    const overview = page.getByRole('button', { name: '全屋查看', exact: true });
    await overview.click();
    assert(await overview.getAttribute('aria-pressed') === 'true', '未进入 OrbitControls 全屋查看模式');
    const reset = page.getByRole('button', { name: '重置视角', exact: true });
    await reset.click();
    const box = await canvas.boundingBox();
    assert(box && box.width > 300 && box.height > 200, 'glTF 画布尺寸无效');
    const point = { x: box.x + box.width * 0.55, y: box.y + box.height * 0.57 };
    const settle = async () => {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.waitForTimeout(100);
    };
    const framesEquivalent = async (first, second) => {
      if (first.equals(second)) return true;
      return page.evaluate(async ({ first, second }) => {
        const decode = async encoded => {
          const binary = atob(encoded);
          const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
          const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
          try {
            const surface = new OffscreenCanvas(bitmap.width, bitmap.height);
            const context = surface.getContext('2d');
            if (!context) throw Error('无法创建截图像素比较画布');
            context.drawImage(bitmap, 0, 0);
            return { width: bitmap.width, height: bitmap.height, pixels: context.getImageData(0, 0, bitmap.width, bitmap.height).data };
          } finally { bitmap.close(); }
        };
        const left = await decode(first), right = await decode(second);
        if (left.width !== right.width || left.height !== right.height) return false;
        for (let index = 0; index < left.pixels.length; index++) {
          if (Math.abs(left.pixels[index] - right.pixels[index]) > 1) return false;
        }
        return true;
      }, { first: first.toString('base64'), second: second.toString('base64') });
    };
    await settle();
    const initial = await canvas.screenshot({ path: 'output/playwright/orbit-gltf-initial.png' });
    await settle();
    assert(await framesEquivalent(initial, await canvas.screenshot()), '初始画面持续变化，无法可靠比较鼠标操作');
    checks.push({ item: 'display-gltf', canvasVisible: true, overviewMode: true, stableFrame: true });

    // 只添加测试时的 DOM 事件观察器，不暴露或修改相机、控制器和渲染器。
    await canvas.evaluate(element => {
      const events = [];
      const listener = event => {
        const entry = { button: event.button, defaultPrevented: event.defaultPrevented };
        events.push(entry);
        queueMicrotask(() => { entry.defaultPrevented = event.defaultPrevented; });
      };
      element.addEventListener('contextmenu', listener);
      element.__orbitMouseCheck = { events, listener };
    });
    const drag = async button => {
      await page.mouse.move(point.x, point.y);
      await page.mouse.down({ button });
      try { await page.mouse.move(point.x + 140, point.y - 45, { steps: 12 }); }
      finally { await page.mouse.up({ button }); }
    };
    const verifyAction = async (item, action) => {
      await action();
      await settle();
      const changed = await canvas.screenshot({ path: 'output/playwright/orbit-gltf-' + item + '.png' });
      assert(!(await framesEquivalent(initial, changed)), 'OrbitControls 操作没有改变场景画面：' + item);
      await settle();
      assert(await framesEquivalent(changed, await canvas.screenshot()), '鼠标释放后画面未稳定：' + item);
      await reset.click();
      await settle();
      const restored = await canvas.screenshot({ path: 'output/playwright/orbit-gltf-' + item + '-reset.png' });
      assert(await framesEquivalent(initial, restored), '重置视角没有恢复初始画面：' + item);
      checks.push({ item, changesView: true, stableOnRelease: true, resetToInitial: true });
    };
    await verifyAction('left-drag-rotate', () => drag('left'));
    await verifyAction('wheel-zoom', async () => {
      await page.mouse.move(point.x, point.y);
      await page.mouse.wheel(0, -420);
    });
    await verifyAction('right-drag-pan', () => drag('right'));
    const contextMenus = await canvas.evaluate(element => element.__orbitMouseCheck.events);
    assert(contextMenus.length > 0, '右键操作未产生可观察的 contextmenu 事件');
    assert(contextMenus.every(event => event.defaultPrevented), '右键拖动未阻止浏览器上下文菜单');
    checks.push({ item: 'right-click-context-menu', events: contextMenus.length, allPrevented: true });
    assert(errors.length === 0, '浏览器出现错误，见 errors');
    checks.push({ item: 'browser-errors', count: 0 });
  } catch (error) {
    errors.push(error instanceof Error ? error.message : String(error));
  } finally {
    if (canvas) await canvas.evaluate(element => {
      const observer = element.__orbitMouseCheck;
      if (observer) element.removeEventListener('contextmenu', observer.listener);
      delete element.__orbitMouseCheck;
    }).catch(() => {});
    page.off('console', consoleError);
    page.off('pageerror', pageError);
    page.off('response', networkResponse);
    page.off('requestfailed', failedRequest);
  }
  return { verifiedAt: new Date().toISOString(), url: page.url(), passed: errors.length === 0, checks, errors };
}
