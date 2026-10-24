## Summary

Describe the change and its user-visible effect.

## Validation

- [ ] Python offline suite: `python -m unittest test_agent_controller test_harness_live_adapter test_sip_teardown`
- [ ] Worker offline suite: `node worker/test-agent.mjs`
- [ ] Documentation links and assets checked, if changed

## Privacy and call safety

- [ ] No credentials, local configuration, private profiles, phone numbers, logs, or call transcripts are included.
- [ ] No live phone call or production gateway change was used as a test.
- [ ] New behavior is labeled implemented, experimental, or planned accurately.
- [ ] Upstream copyright and license notices are preserved where applicable.
