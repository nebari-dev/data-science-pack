---
title: Real-time Collaboration
description: How shared notebook editing works in the JupyterLab image.
---

The JupyterLab image includes
[`jupyter-collaboration`](https://github.com/jupyterlab/jupyter-collaboration), the
official JupyterLab real-time collaboration extension. It lets multiple browser clients
connected to the same running Jupyter server edit notebooks and text files together, with
shared document state and collaborator cursors.

See the upstream
[JupyterLab real-time collaboration docs](https://jupyterlab-realtime-collaboration.readthedocs.io/en/latest/)
and
[configuration reference](https://jupyterlab-realtime-collaboration.readthedocs.io/en/latest/configuration.html)
for extension behavior and tuning options.

In JupyterHub, collaboration across two different users still needs both users to have
access to the same single-user server. Data Science Pack enables user-managed server
sharing by default through `jupyterhub.custom.sharing-scopes-enabled: true`, and also
allows the JupyterLab browser OAuth token to request the share-management scopes used by
the collaboration UI. This includes listing Hub user and group names so the share dialog
can populate its recipient picker.

:::caution[Shared server access is powerful]
Anyone you share a running server with can edit files in that server and execute notebook
code against its kernels as the server owner. Use shared access for trusted collaborators;
use separate project or service accounts when stronger execution isolation is required.
:::


## Runtime storage

The collaboration extension stores document updates on the user's server so clients can
recover shared document state after reconnects. By default, this creates
`.jupyter_ystore.db` in the directory where JupyterLab starts, plus
`.jupyter/collaboration_sessions.json` under the server root directory. These files are
runtime state: do not commit them to source control, and it is safe to delete them when a
server is stopped if you want to discard old collaboration history.

On persistent home volumes, `.jupyter_ystore.db` can grow with document activity. Operators
who need different storage behavior can configure the upstream `YDocExtension` /
`SQLiteYStore` settings through their Jupyter server configuration, for example
`YDocExtension.document_save_delay`, `YDocExtension.document_cleanup_delay`,
`YDocExtension.ystore_class`, or `SQLiteYStore.db_path`.

## Disable user-managed sharing

Set the existing sharing switch to false:

```yaml
jupyterhub:
  custom:
    sharing-scopes-enabled: false
```

With that setting, users can still collaborate through multiple browser tabs/sessions that
already have access to the same server, but ordinary users will not receive the Hub scopes
needed to grant new server shares.

This only prevents new user-managed share grants. Any shares that already exist in
JupyterHub remain active until an admin or a user with the relevant share-management scope
revokes them through JupyterHub's sharing API.
