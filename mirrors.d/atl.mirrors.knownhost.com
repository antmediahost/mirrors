---
name: atl.mirrors.knownhost.com
address:
  http: http://atl.mirrors.knownhost.com/linux/almalinux
  https: https://atl.mirrors.knownhost.com/linux/almalinux
  rsync: rsync://atl.mirrors.knownhost.com/linux/almalinux
update_frequency: 3h
sponsor: KnownHost
sponsor_url: https://www.knownhost.com
email: mirrors@knownhost.com
...