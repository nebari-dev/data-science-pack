import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const lightCodeTheme = prismThemes.github;
const darkCodeTheme = prismThemes.dracula;

const config: Config = {
  title: 'Nebari Data Science Pack',
  tagline: 'Deploy JupyterHub with jhub-apps on Nebari',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://nebari-dev.github.io',
  baseUrl: '/nebari-data-science-pack/',

  organizationName: 'nebari-dev',
  projectName: 'nebari-data-science-pack',

  onBrokenLinks: 'throw',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  markdown: {
    mermaid: true,
  },

  themes: ['@docusaurus/theme-mermaid'],

  plugins: [
    [
      require.resolve('@easyops-cn/docusaurus-search-local'),
      {
        indexBlog: false,
        docsRouteBasePath: '/',
      },
    ],
  ],

  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          sidebarCollapsible: true,
          showLastUpdateTime: true,
          editUrl:
            'https://github.com/nebari-dev/nebari-data-science-pack/edit/main/docs/docs/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      defaultMode: 'light',
      disableSwitch: false,
      respectPrefersColorScheme: true,
    },
    docs: {
      sidebar: {
        hideable: true,
        autoCollapseCategories: true,
      },
    },
    navbar: {
      title: 'Nebari Data Science Pack',
      logo: {
        alt: 'Nebari logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          href: 'https://github.com/nebari-dev/nebari-data-science-pack',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Source',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/nebari-dev/nebari-data-science-pack',
            },
            {
              label: 'Nebari',
              href: 'https://nebari.dev',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Nebari contributors.`,
    },
    prism: {
      theme: lightCodeTheme,
      darkTheme: darkCodeTheme,
      additionalLanguages: ['bash', 'yaml', 'toml', 'python'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
