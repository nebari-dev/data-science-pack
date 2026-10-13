#!/usr/bin/env python3
"""Print the inline Helm values from an ArgoCD Application manifest to stdout.

CI pipes the output into `helm template -f -` so the values embedded in
examples/argocd-application.yaml are rendered through the chart and can't
silently drift from its schema.

Usage: extract_helm_values.py <application.yaml>
"""

import sys

import yaml

manifest = yaml.safe_load(open(sys.argv[1]))
sys.stdout.write(manifest["spec"]["source"]["helm"]["values"])
