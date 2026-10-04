# Agent character assets

`utrace-agent-<colour>.riv` (coral, black, mint, sky, lilac, peach, aqua) are the
final agent characters for the app and the landing page: artboard `AgentCharacter`,
state machine `AgentCharacterMachine`, view model `AgentCharacterModel` with the
fourteen-value enum `state` (`idle, investigating, listening, thinking, speaking,
acknowledgement, concern, building, completion, ship, reconnecting, paused, sleep,
pointer`) and numbers `gazeX`, `gazeY`, `speechEnergy`, `motionScale`. Design,
motion rules and the rebuild tooling are in `../rive/`.

`apps/web/public/agent/` carries byte-identical copies for the web app; refresh
them with `npm run sync:agent-character` in `apps/web` after a re-export (a test
checks them). The coral character is the one the current surfaces load.
