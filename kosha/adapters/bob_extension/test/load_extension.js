"use strict";
// Loads extension.js with the fake `vscode` module in place of Bob's.
const Module = require("module");
const path = require("path");
const fake = require("./fake_vscode");
const orig = Module._load;
Module._load = function (request, ...rest) {
  if (request === "vscode") return fake;
  return orig.call(this, request, ...rest);
};
module.exports = { fake, extension: require(path.join(__dirname, "..", "extension.js")) };
