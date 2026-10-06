# Bundled Markdown renderer

`markdown-it.min.js` is the unmodified browser UMD bundle from markdown-it 15.0.2
(`package/dist/browser/markdown-it.umd.min.js`), distributed under the MIT license
in `markdown-it.LICENSE`.

Source: https://github.com/markdown-it/markdown-it
Package: https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.2.tgz
Verified package SHA-512 (base64):
`q4IGxMv56jCqT4OCRCADBoDP3LO4MhmTXjFbphHPXs4g3j9Xg5RDnxqN8IF/3vIWEU+VCnUq+7JUg/cfy2E6Qw==`

The server embeds this local bundle into the authenticated viewer page. No CDN
request or JavaScript package installation is needed to run MonitorEye.
Raw HTML and Markdown image loading are disabled; links accept HTTP(S) or mailto.
