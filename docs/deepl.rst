.. _deepl#deepl:

DeepL
*****

`DeepL <https://www.deepl.com/>`_ is an online service for machine
translation (MT). Familiarise yourself with DeepL's `terms and conditions
<https://www.deepl.com/pro-license>`_ before you use it.

The Virtaal plugin provides the output of DeepL as suggestions. Your
:doc:`placeables <placeables>` are kept in place and left untranslated. DeepL
is off until you turn it on, and sends nothing until it has your API key.

Getting an API key
==================

DeepL's API has two plans, both of which give you a key:

- **DeepL API Free**: no charge, with a monthly character allowance; DeepL
  stops translating once it is used up, until the next month.
- **DeepL API Pro**: billed by the number of characters translated.

Choose a plan on DeepL's `API plans page
<https://www.deepl.com/pro-api#api-pricing>`_ and sign up. A DeepL API account
is not the same as a DeepL Translator account: if you already have the latter,
DeepL asks you to create a new account for the API. Once signed up, your key is
on the `API Keys <https://www.deepl.com/your-account/keys>`_ page of your
account. Free keys end in ``:fx``; Virtaal uses DeepL's Free or Pro server
according to the key.

Using the key in Virtaal
========================

Open :menuselection:`Edit --> Preferences`, select Translation Memory under
Features and click its :guilabel:`Configure…` button. Tick DeepL in the list of
sources, then click :guilabel:`Configure…` for it and paste your key into
:guilabel:`API key`. The key is kept in the ``[deepl]`` section of ``tm.ini``
in Virtaal's settings folder. Clear the key to stop using DeepL.

If DeepL refuses the key, or its allowance is used up, Virtaal stops asking it
until you change the key or restart Virtaal.

What is sent
============

For each unit you open, Virtaal sends its source text, with placeables
replaced by markers, and the source and target languages to DeepL over HTTPS.
Your key is sent with each request, and is never written to Virtaal's log.
Virtaal asks DeepL only about the unit you are on: looking up units ahead of
time (``prefetch`` in the ``[deepl]`` section of ``tm.ini``, at most 2) is off,
since every character counts towards your allowance or your bill.

:ref:`Fetch Suggestions for This File
<using_virtaal#fetch_suggestions_for_a_whole_file>` can ask DeepL about many
units at once. Its window shows how many characters would be sent and what is
left of your key's allowance for this billing period, and asks for no more than
that.

Since your source text is sent to DeepL, take care that you are allowed to do
that; confidential text probably should not be sent.

Remember that the suggestions from DeepL are unreviewed machine-generated
translations, that could be wrong, inaccurate, or flawed in some other way. It
is meant as a way to help you increase your productivity, not to substitute
the expertise of a human translator.
