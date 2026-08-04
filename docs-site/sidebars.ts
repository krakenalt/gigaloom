import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    {
      type: 'category',
      label: 'Overview',
      collapsible: false,
      items: [
        'index',
        'installation',
        'quickstart',
        'work-threads-and-context',
        {
          type: 'doc',
          id: 'harness',
          label: 'Unified Harness (alpha)',
          key: 'harness',
        },
        {
          type: 'doc',
          id: 'agents-and-multi-agent',
          label: 'Agents and multi-agent behavior',
          key: 'agents-and-multi-agent',
        },
        {
          type: 'doc',
          id: 'agent-runtimes',
          label: 'Install agent runtimes',
          key: 'agent-runtimes',
        },
        'agent-capability-matrix',
      ],
    },
    {
      type: 'category',
      label: 'Operate',
      collapsible: false,
      items: ['operations', 'security', 'gateway-integration'],
    },
    {
      type: 'category',
      label: 'Architecture',
      collapsible: false,
      items: [
        'architecture',
        'architecture/harness',
        'architecture/package-structure',
        'architecture/reliability-and-performance',
        'architecture/session-storage',
        'architecture/capability-admission',
        'architecture/authority-and-approvals',
        'architecture/network-access',
        'architecture/github-permissions',
        'architecture/provider-authentication',
        'architecture/remote-user-identity',
        'architecture/frontend-assets',
        'architecture/operational-guarantees',
        'architecture/work-and-thread-relay',
        'architecture/gateway-routes',
        'architecture/effective-instructions',
        'architecture/local-product-metrics',
      ],
    },
    {
      type: 'category',
      label: 'Project',
      collapsible: false,
      items: ['contributing', 'release', 'source-history'],
    },
  ],
};

export default sidebars;
