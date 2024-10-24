# Browser tests

We use [Cypress](https://www.cypress.io/) to test the high-level features of Modernomad
in a browser. This checks that the frontend code and backend code all work together from
the level of a user clicking around on the site.

Cypress needs to run on your local machine, not inside Docker or a VM, because it has to
start and control your browser. It also assumes your development is running inside
Docker when it clears the database before each test run.

Read through the [docker development][docker-development-environment.md] for how to run the docker container. You also
need to set the Stripe environment variables for the tests to run successfully.

First, install Cypress:

```sh
npm install
```

To run the Cypress tests, make sure your docker instance is running and the migrations
are up to date. The tests also assume that the `generate_test_data` command has been run
(so there is test data in the database). *Warning:* running cypress tests will blow away
your test database, so make sure there's nothing in there you care about.

Then, start it up:

```sh
npm run cypress:open
```

[The Cypress documentation has information on how to run and write tests](https://docs.cypress.io/).
The test cases are in `cypress/fixtures/`.

[1]: docker-development-environment.md
