// docs/astro.config.mjs
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { nebari } from '@nebari/starlight';
import rehypeMermaid from 'rehype-mermaid';
import remarkBaseLinks from './src/plugins/remark-base-links';

// BASE and SITE are set by CI when deploying under a subpath
// (e.g. nebari-dev.github.io/data-science-pack/). Default '/' is the right
// thing for `astro dev` and local previews.
export default defineConfig({
  base: process.env.BASE || '/',
  site: process.env.SITE,
  integrations: [
    starlight({
      title: 'Data Science Pack',
      description:
        'A Helm chart for deploying JupyterHub with jhub-apps on Kubernetes, integrated with the Nebari Operator via the NebariApp CRD.',
      // Shared Nebari identity (brand colors, fonts, logo, favicon, footer, GitHub link)
      // comes from the @nebari/starlight theme plugin. logoHref sets where the header logo
      // takes the reader when they click it — nebari.dev for the project's main site.
      plugins: [nebari({ logoHref: 'https://nebari.dev/' })],
      sidebar: [
        {
          label: 'Overview',
          items: [{ label: 'Introduction', slug: 'index' }],
        },
        {
          label: 'Guides',
          items: [
            { label: 'Quick Start', slug: 'quick-start' },
            { label: 'Architecture', slug: 'architecture' },
            { label: 'Shared Storage', slug: 'shared-storage' },
          ],
        },
        {
          label: 'Administration',
          items: [
            { label: 'Admin setup', slug: 'admin-setup' },
            { label: 'Server profiles', slug: 'server-profiles' },
            { label: 'Nebi integration', slug: 'nebi-integration' },
            { label: 'MLflow integration', slug: 'mlflow-integration' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'Configuration', slug: 'configuration' },
            { label: 'Values reference', slug: 'values-reference' },
            { label: 'NebariApp Integration', slug: 'nebariapp-integration' },
          ],
        },
      ],
    }),
  ],
  markdown: {
    // Turn Shiki off for mermaid so rehype-mermaid sees the raw graph source.
    syntaxHighlight: { type: 'shiki', excludeLangs: ['mermaid'] },
    remarkPlugins: [[remarkBaseLinks, { base: process.env.BASE || '/' }]],
    rehypePlugins: [[rehypeMermaid, { strategy: 'inline-svg' }]],
  },
});
