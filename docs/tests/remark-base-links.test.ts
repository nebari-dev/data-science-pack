import { describe, it, expect } from 'vitest';
import { remark } from 'remark';
import remarkBaseLinks, { prefixUrl } from '../src/plugins/remark-base-links';

describe('prefixUrl', () => {
  it('leaves links unchanged when base is "/"', () => {
    expect(prefixUrl('/configuration/', '/')).toBe('/configuration/');
  });

  it('prefixes a root-absolute link with a sub-path base', () => {
    expect(prefixUrl('/configuration/', '/data-science-pack/')).toBe('/data-science-pack/configuration/');
  });

  it('never rewrites an external link', () => {
    expect(prefixUrl('https://nebari.dev', '/data-science-pack/')).toBe('https://nebari.dev');
  });

  it('never rewrites a protocol-relative link', () => {
    expect(prefixUrl('//example.com/x', '/data-science-pack/')).toBe('//example.com/x');
  });

  it('never rewrites an anchor-only link', () => {
    expect(prefixUrl('#section', '/data-science-pack/')).toBe('#section');
  });

  it('is idempotent on an already-prefixed link', () => {
    expect(prefixUrl('/data-science-pack/configuration/', '/data-science-pack/')).toBe(
      '/data-science-pack/configuration/',
    );
  });

  it('preserves an anchor fragment on an internal link', () => {
    expect(prefixUrl('/configuration/#collector', '/data-science-pack/')).toBe(
      '/data-science-pack/configuration/#collector',
    );
  });
});

describe('remarkBaseLinks plugin', () => {
  it('rewrites link and image urls in a markdown document', async () => {
    const md = 'See [Configuration](/configuration/) and ![img](/img/a.png) and [ext](https://nebari.dev)';
    const out = String(
      await remark().use(remarkBaseLinks, { base: '/data-science-pack/' }).process(md),
    );
    expect(out).toContain('(/data-science-pack/configuration/)');
    expect(out).toContain('(/data-science-pack/img/a.png)');
    expect(out).toContain('(https://nebari.dev)');
  });

  it('is a no-op when base is "/"', async () => {
    const md = '[C](/configuration/)';
    const out = String(await remark().use(remarkBaseLinks, { base: '/' }).process(md));
    expect(out).toContain('(/configuration/)');
  });
});
